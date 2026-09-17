#!/usr/bin/env python3
"""Translate missing Spanish subtitles to English-present episodes/movies via Bazarr's
Gemini translator. Re-derives its worklist from Bazarr's live wanted list on every run,
so it's safe to stop and re-run at any time - already-completed items simply won't be
missing Spanish anymore and will drop off the list.

Stops after CONSECUTIVE_FAILURE_LIMIT items in a row fail/timeout, on the assumption
that's a real problem (an API outage, or a run of specifically-broken items) rather than
bad luck - rather than grinding uselessly through the rest of a long backlog.

Failures are classified by checking Bazarr's own log (BAZARR_LOG_PATH) for what actually
happened, instead of trusting the generic "no output file appeared" signal alone:

- Gemini daily quota exhausted (confirmed 2026-09-16: 500 requests/day for
  gemini-3.5-flash-lite, free tier, resets at midnight Pacific) affects every item
  indiscriminately, so it is NOT recorded as a per-item failure - it stops the run
  immediately and writes a cooldown until the next reset. Any cron-triggered run during
  the cooldown is a fast no-op: no API calls, no blocklist changes.
- A permission error reading the source subtitle (recurring bug, cause still unknown -
  see gotcha_subtitle_zero_perm_files in project memory) says nothing about whether the
  item is actually translatable, so it's also NOT recorded as a per-item failure - just
  skipped for this run. It still counts toward the consecutive-failure circuit breaker,
  since a cluster of permission errors (e.g. a whole show hit by the zero-perm bug at
  once) is exactly the kind of "stop and look at this" situation that breaker exists for.
- Gemini returning a mismatched number of translated lines (a real, reproducible property
  of some episodes - long-form narration-heavy content like documentaries can push a
  300-line batch past Gemini's output token budget) IS recorded as a per-item failure,
  same as before, since it's specific to that item's content rather than a systemic
  problem - it just gets a precise log line instead of a generic TIMEOUT now.
- Anything else (a genuine hang, or a cause not covered above) still falls through to the
  original generic TIMEOUT after the full poll window.

Items that fail/timeout are recorded in BLOCKLIST_PATH with an attempt count. An item is
only excluded from future worklists once it has failed on SKIP_AFTER_ATTEMPTS separate
runs, so a single transient failure doesn't permanently exclude a translatable item, but a
consistently broken item stops eating the consecutive-failure budget on every run and
blocking everything queued behind it.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = "http://localhost:6767/api"
CONTAINER_MEDIA_PREFIX = "/data"
HOST_MEDIA_PREFIX = "/mnt/media"
BAZARR_CONFIG_PATH = "/srv/docker/bazarr/config/config.yaml"
BAZARR_LOG_PATH = "/srv/docker/bazarr/log/bazarr.log"
BAZARR_LOG_TZ = ZoneInfo("America/Chicago")  # bazarr.log timestamps have no offset; this is the container's local tz
QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")  # Gemini free-tier daily quota resets at midnight Pacific

RATE_LIMIT_MARKERS = ("rate limited", "RESOURCE_EXHAUSTED")
PERMISSION_ERROR_MARKERS = ("PermissionError", "Permission denied")
MALFORMED_JSON_MARKERS = (
    "Gemini returned", "Gemini has returned different indices",
    "Expecting ','", "Expecting value", "Extra data",
    "Invalid control character", "Invalid \\uXXXX escape",
)


def _load_apikey():
    env_key = os.environ.get("BAZARR_APIKEY")
    if env_key:
        return env_key
    try:
        import yaml
        with open(BAZARR_CONFIG_PATH) as f:
            return yaml.safe_load(f)["auth"]["apikey"]
    except Exception:
        return None


APIKEY = _load_apikey()
if not APIKEY:
    sys.exit("Could not determine Bazarr API key: set BAZARR_APIKEY or ensure "
             f"{BAZARR_CONFIG_PATH} has auth.apikey set")
LOG_PATH = "/srv/docker/bazarr/config/translate-missing-es.log"
BLOCKLIST_PATH = "/srv/docker/bazarr/config/translate-missing-es-blocklist.json"
COOLDOWN_PATH = "/srv/docker/bazarr/config/translate-missing-es-cooldown.json"
POLL_INTERVAL = 8
POLL_TIMEOUT = 240  # seconds per item
CONSECUTIVE_FAILURE_LIMIT = 5
SKIP_AFTER_ATTEMPTS = 2  # exclude an item from the worklist after this many failed runs


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_attempts():
    try:
        with open(BLOCKLIST_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"episode": {}, "movie": {}}


def record_failure(kind, item_id):
    attempts = load_attempts()
    key = str(item_id)
    attempts[kind][key] = attempts[kind].get(key, 0) + 1
    with open(BLOCKLIST_PATH, "w") as f:
        json.dump(attempts, f)
    return attempts[kind][key]


def read_cooldown_until():
    try:
        with open(COOLDOWN_PATH, "r") as f:
            data = json.load(f)
        return datetime.fromisoformat(data["until"])
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
        return None


def write_cooldown_until_next_quota_reset():
    now_pacific = datetime.now(QUOTA_RESET_TZ)
    next_midnight_pacific = (now_pacific + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    until_utc = next_midnight_pacific.astimezone(timezone.utc)
    with open(COOLDOWN_PATH, "w") as f:
        json.dump({"until": until_utc.isoformat()}, f)
    return until_utc


def classify_bazarr_log_error_since(since_utc, path_hint):
    """Tail bazarr.log since since_utc (aware UTC) for a known error category.

    Rate limiting is checked globally (it affects every request, not just ours).
    Permission and malformed-JSON errors are only attributed to us if the log line
    also mentions path_hint (the container-path subtitle file we're waiting on),
    so an unrelated background job's error doesn't get misattributed to this item.

    Returns (category, snippet) where category is one of "rate_limited",
    "permission_error", "malformed_json", or (None, None) if nothing matched.
    """
    try:
        with open(BAZARR_LOG_PATH, "r", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            lines = f.readlines()
    except FileNotFoundError:
        return None, None

    cutoff = since_utc - timedelta(seconds=5)
    for line in reversed(lines):
        if "|" not in line:
            continue
        ts_str = line.split("|", 1)[0].strip()
        try:
            local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BAZARR_LOG_TZ)
        except ValueError:
            continue
        if local_dt.astimezone(timezone.utc) < cutoff:
            break  # log is chronological; everything older is irrelevant

        if any(m in line for m in RATE_LIMIT_MARKERS):
            return "rate_limited", None

        # Malformed-JSON error lines never mention which file they're about, unlike
        # permission errors - but since this script only ever has one Gemini call in
        # flight at a time, any such error in our time window has to be about our
        # current request, so no path match is needed (or possible) here.
        if any(m in line for m in MALFORMED_JSON_MARKERS):
            return "malformed_json", line.strip()[:300]

        if path_hint and path_hint in line and any(m in line for m in PERMISSION_ERROR_MARKERS):
            return "permission_error", line.strip()[:300]

    return None, None


def api_get(path, params=None):
    args = ["curl", "-s", f"{BASE}{path}", "-H", f"X-API-KEY: {APIKEY}"]
    if params:
        for k, v in params.items():
            args += ["--data-urlencode", f"{k}={v}", "-G"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout)


def api_patch(data):
    args = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "PATCH",
            f"{BASE}/subtitles", "-H", f"X-API-KEY: {APIKEY}"]
    for k, v in data.items():
        args += ["--data-urlencode", f"{k}={v}"]
    out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def get_wanted(kind, attempts):
    path = f"/episodes/wanted" if kind == "episode" else "/movies/wanted"
    d = api_get(path, {"start": 0, "length": -1})
    id_key = "sonarrEpisodeId" if kind == "episode" else "radarrId"
    blocked = {k for k, v in attempts[kind].items() if v >= SKIP_AFTER_ATTEMPTS}
    return [x for x in d["data"]
            if any(m["code2"] == "es" for m in x["missing_subtitles"])
            and not any(m["code2"] == "en" for m in x["missing_subtitles"])
            and str(x[id_key]) not in blocked]


def get_detail(kind, item_id):
    if kind == "episode":
        d = api_get("/episodes", {"episodeid[]": item_id})
    else:
        d = api_get("/movies", {"radarrid[]": item_id})
    return d["data"][0] if d.get("data") else None


def find_english_external_sub(detail):
    for s in detail.get("subtitles", []):
        if s.get("code2") == "en" and s.get("embedded_track_id") is None and s.get("path"):
            return s
    return None


def build_es_target_path(en_path, forced, hi):
    base = en_path.rsplit(".en", 1)[0] if ".en" in en_path else en_path.rsplit(".", 1)[0]
    suffix = ".es"
    if forced:
        suffix += ".forced"
    if hi:
        suffix += ".hi"
    target = base + suffix + ".srt"
    if target.startswith(CONTAINER_MEDIA_PREFIX):
        target = HOST_MEDIA_PREFIX + target[len(CONTAINER_MEDIA_PREFIX):]
    return target


def process_one(kind, item):
    item_id = item["sonarrEpisodeId"] if kind == "episode" else item["radarrId"]
    series_id = item.get("sonarrSeriesId")
    title = item.get("seriesTitle") or item.get("title")
    epno = item.get("episode_number", "")

    detail = get_detail(kind, item_id)
    if not detail:
        log(f"SKIP {kind} {item_id} ({title} {epno}): could not fetch detail")
        return "skip"

    en_sub = find_english_external_sub(detail)
    if not en_sub:
        log(f"SKIP {kind} {item_id} ({title} {epno}): no external English subtitle to translate from")
        return "skip"

    wanted_es = next((m for m in item["missing_subtitles"] if m["code2"] == "es"), None)
    forced = bool(wanted_es.get("forced")) if wanted_es else False
    hi = bool(wanted_es.get("hi")) if wanted_es else False

    target_path = build_es_target_path(en_sub["path"], forced, hi)

    patch_data = {
        "action": "translate",
        "language": "es",
        "path": en_sub["path"],
        "type": kind,
        "id": item_id,
        "forced": "True" if forced else "False",
        "hi": "True" if hi else "False",
    }
    if kind == "episode":
        patch_data["seriesid"] = series_id

    request_time = datetime.now(timezone.utc)
    code = api_patch(patch_data)
    if code != "204":
        n = record_failure(kind, item_id)
        log(f"FAIL {kind} {item_id} ({title} {epno}): translate call returned HTTP {code} "
            f"(attempt {n})")
        return "fail"

    waited = 0
    while waited < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        try:
            with open(target_path, "r") as f:
                content = f.read()
            if content.strip():
                log(f"OK {kind} {item_id} ({title} {epno}): {target_path} ({len(content)} bytes, {waited}s)")
                return "ok"
        except FileNotFoundError:
            pass

        category, snippet = classify_bazarr_log_error_since(request_time, en_sub["path"])

        if category == "rate_limited":
            log(f"RATE_LIMITED {kind} {item_id} ({title} {epno}): Gemini daily quota exhausted "
                f"after {waited}s - not counting against this item")
            return "rate_limited"

        if category == "permission_error":
            log(f"PERMISSION_ERROR {kind} {item_id} ({title} {epno}) after {waited}s - "
                f"not counting against this item, will retry once fixed: {snippet}")
            return "permission_error"

        if category == "malformed_json":
            n = record_failure(kind, item_id)
            log(f"MALFORMED_JSON {kind} {item_id} ({title} {epno}) after {waited}s "
                f"(attempt {n}{', excluding from future worklists' if n >= SKIP_AFTER_ATTEMPTS else ''}): "
                f"{snippet}")
            return "malformed_json"

    n = record_failure(kind, item_id)
    log(f"TIMEOUT {kind} {item_id} ({title} {epno}): no output after {POLL_TIMEOUT}s "
        f"(attempt {n}{', excluding from future worklists' if n >= SKIP_AFTER_ATTEMPTS else ''})")
    return "timeout"


def main():
    cooldown_until = read_cooldown_until()
    now = datetime.now(timezone.utc)
    if cooldown_until and now < cooldown_until:
        log(f"=== translate-missing-es skipped: in cooldown until {cooldown_until.isoformat()} "
            f"(Gemini daily quota exhausted) ===")
        return

    log("=== translate-missing-es run started ===")
    consecutive_failures = 0
    counts = {"ok": 0, "fail": 0, "timeout": 0, "skip": 0,
              "rate_limited": 0, "permission_error": 0, "malformed_json": 0}

    for kind in ("episode", "movie"):
        attempts = load_attempts()
        worklist = get_wanted(kind, attempts)
        excluded = sum(1 for v in attempts[kind].values() if v >= SKIP_AFTER_ATTEMPTS)
        log(f"{kind}: {len(worklist)} candidates (Spanish missing, English present)"
            + (f", {excluded} excluded as repeatedly-failing" if excluded else ""))
        for item in worklist:
            result = process_one(kind, item)
            counts[result] += 1
            if result == "rate_limited":
                until = write_cooldown_until_next_quota_reset()
                log(f"STOPPING: Gemini daily quota exhausted, cooling down until "
                    f"{until.isoformat()} - not blocklisting the item in progress.")
                log(f"=== run ended early: {counts} ===")
                return
            if result in ("fail", "timeout", "malformed_json", "permission_error"):
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                log(f"STOPPING: {consecutive_failures} consecutive failures/timeouts — "
                    f"likely a run of specifically-broken items or an API problem, not bad luck. "
                    f"Re-run this script later to resume where it left off.")
                log(f"=== run ended early: {counts} ===")
                return

    log(f"=== run completed: {counts} ===")


if __name__ == "__main__":
    main()
