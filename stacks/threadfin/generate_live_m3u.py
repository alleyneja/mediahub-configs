#!/usr/bin/env python3
"""
Generates a live-only M3U from the Xtream Codes API and saves it locally.
Run this script to refresh the channel list (e.g. via cron daily).
"""
import json
import urllib.request
import sys

USERNAME = "alleyneja"
PASSWORD = "68fcMcytqb"
HOST = "http://ky-tv.cc:80"
OUTPUT = "/srv/docker/threadfin/conf/live_only.m3u"

def fetch(action, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{HOST}/player_api.php?username={USERNAME}&password={PASSWORD}&action={action}&{query}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

print("Fetching live categories...")
categories = fetch("get_live_categories")
cat_map = {c["category_id"]: c["category_name"] for c in categories}
print(f"  {len(categories)} categories")

print("Fetching live streams...")
streams = fetch("get_live_streams")
print(f"  {len(streams)} streams")

print(f"Writing M3U to {OUTPUT}...")
with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for s in streams:
        name = s.get("name", "")
        stream_id = s.get("stream_id", "")
        logo = s.get("stream_icon", "")
        epg_id = s.get("epg_channel_id", "")
        cat_id = str(s.get("category_id", ""))
        group = cat_map.get(cat_id, "Uncategorized")
        url = f"{HOST}/{USERNAME}/{PASSWORD}/{stream_id}.ts"
        f.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}\n')
        f.write(f"{url}\n")

print(f"Done. {len(streams)} live streams written.")
