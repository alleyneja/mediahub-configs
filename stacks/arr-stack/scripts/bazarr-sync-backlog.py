#!/usr/bin/env python3
"""One-time batch job (2026-09-18): run Bazarr's audio-based subtitle sync (ffsubsync)
against a fixed list of episodes that never got a sync pass, or got clamped at the old
60-second max_offset_seconds ceiling. See project memory for why:

- 547 translated Spanish subtitles (our bazarr-translate-missing-es.py output) had NEVER
  been through a sync pass at all - Bazarr's own postprocess_subtitles(), called after a
  translation, does chmod + library indexing + Plex/Jellyfin refresh, but never calls
  sync_subtitles(). They only ever had the English source's timestamps copied verbatim.
- 31 subtitles (any language) hit the old 60s cap and may have been under-corrected.

Target list is a fixed snapshot (/tmp/sync_targets.json) taken once before this run, not
re-derived live, since this is a one-time backlog cleanup, not an ongoing job like the
translation script. Same "fire the request, then poll for real completion" pattern as
bazarr-translate-missing-es.py, since Bazarr's sync action is also an async job under the
hood - the PATCH call returns 204 immediately, the actual audio decode + alignment happens
later in Bazarr's own job queue.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BAZARR_LOCAL_TZ = ZoneInfo("America/Chicago")  # history API's parsed_timestamp has no offset

BASE = "http://localhost:6767/api"
BAZARR_CONFIG_PATH = "/srv/docker/bazarr/config/config.yaml"
TARGETS_PATH = "/tmp/sync_targets.json"
LOG_PATH = "/home/jay/logs/bazarr-sync-backlog.log"
POLL_INTERVAL = 8
POLL_TIMEOUT = 240  # seconds per item - generous for audio decode on long/large files
MAX_OFFSET_SECONDS = "120"


def _load_apikey():
    import yaml
    with open(BAZARR_CONFIG_PATH) as f:
        return yaml.safe_load(f)["auth"]["apikey"]


APIKEY = _load_apikey()


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
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}


def api_patch(data):
    args = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "PATCH",
            f"{BASE}/subtitles", "-H", f"X-API-KEY: {APIKEY}"]
    for k, v in data.items():
        args += ["--data-urlencode", f"{k}={v}"]
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "timeout"
    return out.stdout.strip()


def latest_sync_entry_since(episode_id, subtitles_path, since_utc):
    """Poll episode history for a new sync (action=5) or relevant error entry for this
    exact subtitle file, appearing after since_utc."""
    d = api_get("/episodes/history", {"start": 0, "length": 20, "episodeid": episode_id})
    for x in d.get("data", []):
        if x.get("subtitles_path") != subtitles_path:
            continue
        ts = x.get("parsed_timestamp")
        if not ts:
            continue
        try:
            local_dt = datetime.strptime(ts, "%m/%d/%y %H:%M:%S").replace(tzinfo=BAZARR_LOCAL_TZ)
        except ValueError:
            continue
        entry_dt = local_dt.astimezone(timezone.utc)
        if entry_dt >= since_utc - timedelta(seconds=10) and x.get("action") == 5:
            return x.get("description")
    return None


def sync_one(target):
    episode_id = target["sonarrEpisodeId"]
    path = target["subtitles_path"]
    title = f"{target.get('seriesTitle')} ({target['reason']})"

    request_time = datetime.now(timezone.utc)
    code = api_patch({
        "action": "sync",
        "language": target["language"],
        "path": path,
        "type": "episode",
        "id": episode_id,
        "forced": "True" if target["forced"] else "False",
        "hi": "True" if target["hi"] else "False",
        "max_offset_seconds": MAX_OFFSET_SECONDS,
    })
    if code != "204":
        log(f"FAIL {episode_id} ({title}): PATCH returned HTTP {code} - {path}")
        return "fail"

    waited = 0
    while waited < POLL_TIMEOUT:
        time.sleep(POLL_INTERVAL)
        waited += POLL_INTERVAL
        description = latest_sync_entry_since(episode_id, path, request_time)
        if description:
            log(f"OK {episode_id} ({title}) after {waited}s: {description} - {path}")
            return "ok"

    log(f"TIMEOUT {episode_id} ({title}): no sync history entry after {POLL_TIMEOUT}s - {path}")
    return "timeout"


def main():
    with open(TARGETS_PATH) as f:
        targets = json.load(f)

    log(f"=== bazarr-sync-backlog run started: {len(targets)} targets, "
        f"max_offset_seconds={MAX_OFFSET_SECONDS} ===")
    counts = {"ok": 0, "fail": 0, "timeout": 0}

    for i, target in enumerate(targets, 1):
        result = sync_one(target)
        counts[result] += 1
        if i % 25 == 0:
            log(f"--- progress: {i}/{len(targets)} processed, {counts} so far ---")

    log(f"=== bazarr-sync-backlog run completed: {counts} ===")


if __name__ == "__main__":
    main()
