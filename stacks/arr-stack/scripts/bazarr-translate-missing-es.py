#!/usr/bin/env python3
"""Translate missing Spanish subtitles to English-present episodes/movies via Bazarr's
Gemini translator. Re-derives its worklist from Bazarr's live wanted list on every run,
so it's safe to stop and re-run at any time - already-completed items simply won't be
missing Spanish anymore and will drop off the list.

Stops after CONSECUTIVE_FAILURE_LIMIT items in a row fail/timeout, on the assumption
that's a real problem (quota exhaustion, API outage) rather than bad luck - rather than
grinding uselessly through the rest of a long backlog.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE = "http://localhost:6767/api"
APIKEY = os.environ.get("BAZARR_APIKEY")
if not APIKEY:
    sys.exit("BAZARR_APIKEY environment variable is required (read it from "
             "/srv/docker/bazarr/config/config.yaml under auth.apikey)")
LOG_PATH = "/srv/docker/bazarr/config/translate-missing-es.log"
POLL_INTERVAL = 8
POLL_TIMEOUT = 240  # seconds per item
CONSECUTIVE_FAILURE_LIMIT = 5


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


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


def get_wanted(kind):
    path = f"/episodes/wanted" if kind == "episode" else "/movies/wanted"
    d = api_get(path, {"start": 0, "length": -1})
    return [x for x in d["data"]
            if any(m["code2"] == "es" for m in x["missing_subtitles"])
            and not any(m["code2"] == "en" for m in x["missing_subtitles"])]


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
    return base + suffix + ".srt"


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
        log(f"FAIL {kind} {item_id} ({title} {epno}): translate call returned HTTP {code}")
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

    log(f"TIMEOUT {kind} {item_id} ({title} {epno}): no output after {POLL_TIMEOUT}s")
    return "timeout"


def main():
    log("=== translate-missing-es run started ===")
    consecutive_failures = 0
    counts = {"ok": 0, "fail": 0, "timeout": 0, "skip": 0}

    for kind in ("episode", "movie"):
        worklist = get_wanted(kind)
        log(f"{kind}: {len(worklist)} candidates (Spanish missing, English present)")
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
