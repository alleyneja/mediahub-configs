#!/usr/bin/env python3
"""Translate missing Spanish subtitles to English-present episodes/movies via Bazarr's
Gemini translator. Re-derives its worklist from Bazarr's live wanted list on every run,
so it's safe to stop and re-run at any time - already-completed items simply won't be
missing Spanish anymore and will drop off the list.

Stops after CONSECUTIVE_FAILURE_LIMIT items in a row fail/timeout, on the assumption
that's a real problem (quota exhaustion, API outage) rather than bad luck - rather than
grinding uselessly through the rest of a long backlog.

Items that fail/timeout are recorded in BLOCKLIST_PATH with an attempt count. An item is
only excluded from future worklists once it has failed on SKIP_AFTER_ATTEMPTS separate
runs, so a single transient failure (e.g. an outage mid-run) doesn't permanently exclude
a translatable item, but a consistently broken item (e.g. one that reproducibly makes
Gemini return malformed output) stops eating the consecutive-failure budget on every run
and blocking everything queued behind it.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "http://localhost:6767/api"
CONTAINER_MEDIA_PREFIX = "/data"
HOST_MEDIA_PREFIX = "/mnt/media"
BAZARR_CONFIG_PATH = "/srv/docker/bazarr/config/config.yaml"


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
            continue

    n = record_failure(kind, item_id)
    log(f"TIMEOUT {kind} {item_id} ({title} {epno}): no output after {POLL_TIMEOUT}s "
        f"(attempt {n}{', excluding from future worklists' if n >= SKIP_AFTER_ATTEMPTS else ''})")
    return "timeout"


def main():
    log("=== translate-missing-es run started ===")
    consecutive_failures = 0
    counts = {"ok": 0, "fail": 0, "timeout": 0, "skip": 0}

    for kind in ("episode", "movie"):
        attempts = load_attempts()
        worklist = get_wanted(kind, attempts)
        excluded = sum(1 for v in attempts[kind].values() if v >= SKIP_AFTER_ATTEMPTS)
        log(f"{kind}: {len(worklist)} candidates (Spanish missing, English present)"
            + (f", {excluded} excluded as repeatedly-failing" if excluded else ""))
        for item in worklist:
            result = process_one(kind, item)
            counts[result] += 1
            if result in ("fail", "timeout"):
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                log(f"STOPPING: {consecutive_failures} consecutive failures/timeouts — "
                    f"likely quota exhaustion or an API problem, not bad luck. "
                    f"Re-run this script later to resume where it left off.")
                log(f"=== run ended early: {counts} ===")
                sys.exit(1)

    log(f"=== run completed: {counts} ===")


if __name__ == "__main__":
    main()
