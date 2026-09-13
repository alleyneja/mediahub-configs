#!/usr/bin/env python3
"""
Pulls real programme listings from Schedules Direct's JSON API for a curated
set of channels and writes them as a local XMLTV file for Threadfin to merge
in alongside the provider's own guide data.

Uses the SAME tvg-ids our IPTV provider's M3U already assigns to these
channels (see stacks/threadfin/generate_live_m3u.py) so Threadfin's existing
multi-source XMLTV merge picks this up automatically — no xepg.json/Plex
channel remapping required.

Credentials live in a sibling .env file (SD_USERNAME / SD_PASSWORD), never
in this script — this file is committed to a public repo.

Run this on a daily cron, same pattern as generate_live_m3u.py.
"""
import json
import os
import hashlib
import urllib.request
import urllib.error
from datetime import date, timedelta, datetime
from xml.sax.saxutils import escape

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")
OUTPUT = "/srv/docker/threadfin/conf/schedulesdirect-epg/guide.xml"
API_BASE = "https://json.schedulesdirect.org/20141201"
DAYS_AHEAD = 7

# Our existing Threadfin tvg-id -> Schedules Direct stationID (lineup:
# USA-DITV534-X, "DIRECTV Orlando" — chosen because it's the one lineup that
# carries both the Orlando OTA locals we curate AND the national cable sports
# networks, in the same account). HD variants used where available; SD
# duplicates SD/HD feeds with separate stationIDs but identical schedules.
STATION_MAP = {
    "secnetwork.us": "89714",    # SEC Network HD
    "espn.us": "32645",          # ESPN HD
    "espn2.us": "45507",         # ESPN2 HD
    "espnnews.us": "59976",      # ESPNEWS HD
    "espnu.us": "60696",         # ESPNU HD
    "accnetwork.us": "111871",   # ACC Network
    "foxwofl.us": "21221",       # WOFL-DT (FOX 35 Orlando)
    "abcwftv.us": "20491",       # WFTV-DT (ABC 9 Orlando)
    "cbs6wkmg.us": "21299",      # WKMG-DT (CBS 6 Orlando)
    "nbcwesh.us": "21647",       # WESH-DT (NBC 2 Orlando)
}


def load_env():
    creds = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds


def get_token(username, password):
    pwhash = hashlib.sha1(password.encode()).hexdigest()
    req = urllib.request.Request(
        f"{API_BASE}/token",
        data=json.dumps({"username": username, "password": pwhash}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.load(r)
    if resp.get("code", 0) != 0:
        raise SystemExit(f"ABORT: Schedules Direct auth failed: {resp}")
    return resp["token"]


def api_post(token, path, body):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"token": token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def xmltv_time(iso_str, extra_seconds=0):
    dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=extra_seconds)
    return dt.strftime("%Y%m%d%H%M%S") + " +0000"


def main():
    creds = load_env()
    token = get_token(creds["SD_USERNAME"], creds["SD_PASSWORD"])

    dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(DAYS_AHEAD)]

    schedules_by_channel = {}
    all_program_ids = set()
    for tvg_id, station_id in STATION_MAP.items():
        sched = api_post(token, "/schedules", [{"stationID": station_id, "date": dates}])
        programs = []
        for day in sched:
            programs.extend(day.get("programs", []))
        schedules_by_channel[tvg_id] = programs
        all_program_ids.update(p["programID"] for p in programs)

    details_map = {}
    all_program_ids = list(all_program_ids)
    for i in range(0, len(all_program_ids), 500):
        chunk = all_program_ids[i:i + 500]
        for d in api_post(token, "/programs", chunk):
            details_map[d["programID"]] = d

    channel_blocks = []
    programme_blocks = []
    total_programmes = 0

    for tvg_id, programs in schedules_by_channel.items():
        if not programs:
            continue
        channel_blocks.append(
            f'  <channel id="{escape(tvg_id)}">\n'
            f"    <display-name>{escape(tvg_id)}</display-name>\n"
            f"  </channel>"
        )
        for p in programs:
            details = details_map.get(p["programID"], {})
            titles = details.get("titles", [])
            title = titles[0].get("title120", "Unknown") if titles else "Unknown"
            subtitle = details.get("episodeTitle150", "")

            desc = ""
            descs = details.get("descriptions", {})
            if descs.get("description1000"):
                desc = descs["description1000"][0].get("description", "")
            elif descs.get("description100"):
                desc = descs["description100"][0].get("description", "")

            start = xmltv_time(p["airDateTime"])
            stop = xmltv_time(p["airDateTime"], extra_seconds=p.get("duration", 0))

            block = [f'  <programme start="{start}" stop="{stop}" channel="{escape(tvg_id)}">']
            block.append(f"    <title>{escape(title)}</title>")
            if subtitle:
                block.append(f"    <sub-title>{escape(subtitle)}</sub-title>")
            if desc:
                block.append(f"    <desc>{escape(desc)}</desc>")
            block.append("  </programme>")
            programme_blocks.append("\n".join(block))
            total_programmes += 1

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    tmp_path = OUTPUT + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tv generator-info-name="mediahub-schedulesdirect">\n')
        f.write("\n".join(channel_blocks) + "\n")
        f.write("\n".join(programme_blocks) + "\n")
        f.write("</tv>\n")
    os.replace(tmp_path, OUTPUT)

    print(f"Wrote {total_programmes} programmes across {len(channel_blocks)} channels to {OUTPUT}")


if __name__ == "__main__":
    main()
