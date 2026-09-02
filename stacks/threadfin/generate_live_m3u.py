#!/usr/bin/env python3
"""
Generates a curated live-only M3U from the Xtream Codes API.
Run this script to refresh the channel list (cron refreshes daily at 4am).
"""
import json
import os
import tempfile
import urllib.request

USERNAME = "alleyneja"
PASSWORD = "***REMOVED-IPTV-PASSWORD***"
HOST = "http://link4tv.cc:80"
OUTPUT = "/srv/docker/threadfin/conf/live_only.m3u"

# Refuse to publish a lineup smaller than this. The provider can answer 200 OK with an
# empty or truncated stream list (expired auth, upstream hiccup); without this floor the
# nightly cron would happily overwrite a working 361-channel M3U with a header and nothing
# else. Current lineup is ~361, so 100 is a wide margin that still catches a wipe.
MIN_CHANNELS = 100

# Override broken/missing provider logos with stable ones (matched by name substring,
# case-insensitive). The provider's EPG lists a dead Bing search-thumbnail as CBS's
# primary icon (Threadfin picks the first <icon>, which 404s). Point it at the
# provider's OWN logo CDN (185.193.88.130) instead — same source Threadfin already
# caches successfully for every other channel. Requires x-update-channel-icon=True on
# the channel in xepg.json so Threadfin uses this M3U logo over the EPG icon.
LOGO_OVERRIDES = {
    "CBS 2 WFMY GREENSBORO": "http://185.193.88.130:80/images/d90cbfe9c7b343dd2eafef8117272284.png",
    # CBS 6 WKMG ORLANDO: every logo the provider offers for this channel is dead —
    # its own tvg-logo/EPG icon (Bing thumbnail, 404s) AND the "mega-list" duplicate
    # entry's icon (23.227.147.172 returns HTTP 200 but the body is a Swift object-
    # storage "File not found" error page, not an image — check content, not just
    # status). Using the real station logo from Wikimedia Commons instead, which is
    # in fact the exact file the provider's dead link was trying to mirror (its
    # error body literally names ".../WKMG-TV_Logo.jpg").
    "CBS 6 WKMG ORLANDO": "https://upload.wikimedia.org/wikipedia/commons/d/d7/WKMG-TV_Logo.jpg",
    # NBC 2 WESH ORLANDO: same story — its own tvg-logo (23.227.147.172) is also a
    # broken-storage error page, not an image. The provider's "mega-list" duplicate
    # entry (USA NBC Orlando WESH, stream 648655) has a genuinely live logo on the
    # same host, confirmed by fetching and viewing it.
    "NBC 2 WESH ORLANDO": "http://23.227.147.172:80/images/5b452bb5b4568013045bb1dedce45e80.png",
}

# Override a missing epg_channel_id (matched by name substring, case-insensitive).
# The provider gives this channel no epg_channel_id at all, which writes a literal
# tvg-id="None" into the M3U. Threadfin then treats it as an unmapped/inactive
# channel and silently backs its "url" field with a shared placeholder stream (a
# dead-channel filler loop, not real content) EVERY time it re-ingests the
# playlist — a direct xepg.json "url" edit gets clobbered on the next restart, so
# the fix has to happen here, upstream of Threadfin, not in xepg.json.
# nbcwesh.us is the real guide id for this affiliate group, borrowed from the
# WESH Daytona Beach duplicate feed which does carry it (confirmed real programme
# data). Broke live playback for ~30min on 2026-09-02 before this was found.
EPG_ID_OVERRIDES = {
    "NBC 2 WESH ORLANDO": "nbcwesh.us",
}

# Groups included entirely — no name filtering applied
FULL_GROUPS = {
    "USA Latin UNIVISION",
    "USA Latin TELEMUNDO",
    "USA Latin GALAVISION",
    "USA Latin UNIMAS",
    "MX: Mexico Entertainment",
    "MX: Mexico General",
    "MX: Mexico News",
    "MX: Mexico Kids",
    "MX: Mexico Sports",
}

# Groups included only if channel name matches one of the listed keywords (case-insensitive)
CURATED_GROUPS = {
    "USA Sports": [
        "ESPN HD", "ESPN 2", "ESPN NEWS HD", "ESPN U HD", "ESPN SEC Network",
        "FOX SPORTS 1 HD", "FOX SPORTS 2 HD", "CBS Sports Network",
        "NFL Redzone", "Golf LHD", "Tennis Channel",
        "ACC Network", "Big Ten Network UHD", "PAC-12 Mountain",
        "UFC Fight Pass", "Fight Network", "MavTV Motorsports",
        "Olympic Channel", "Stadium 1", "FS1", "FS2",
        "NHL NETWORK HD", "CBS Sports Golazo",
        "FOX DEPORTES HD",
    ],
    "USA NBC Sports": [
        "NBC GOLF HD",
    ],
    "USA News": [
        "CNN UHD", "CNN INTERNATIONAL", "CNN*", "Fox News Channel",
        "Fox Business UHD", "CNBC UHD", "C-SPAN", "C-SPAN 2", "C-SPAN 3",
        "AL JAZEERA HD", "NewsNation", "NEWSMAX", "Newsmax 2",
        "OAN (One America News Network)", "BBC World News",
        "Bloomberg", "HLN", "FOX Weather", "Scripps News",
    ],
    "USA Entertainment": [
        "AMC", "A&E UHD", "Animal Planet East UHD", "BET East", "BET HER",
        "Bravo East", "Comedy Central FHD", "Discovery East", "Discovery Family",
        "E! Entertainment", "Food Network East UHD", "Freeform",
        "FX", "GSN (Game Show Network)", "Hallmark Channel",
        "Hallmark Movies & Mysteries UHD", "HGTV East UHD", "History East UHD",
        "IFC", "Lifetime East UHD", "MTV East UHD", "Nat Geo East",
        "Syfy East", "TBS East", "TLC East", "TNT East FHD",
        "truTV East", "USA Network East", "VH1 UHD",
        "BBC America HD", "The Weather Channel", "Oxygen East",
        "Paramount East UHD", "Science Channel",
    ],
    "USA Movies Channels": [
        "HBO East", "HBO Comedy", "HBO Drama", "HBO Hits East", "HBO West",
        "Showtime Showcase", "Showtime Extreme", "Showtime 2",
        "Cinemax East", "Cinemax Hits East",
        "StarZ East", "Starz Cinema", "Starz Encore East", "Starz in Black East",
        "TCM", "Turner Classic Movies", "FXM", "FX East UHD",
        "EPIX", "EPIX2", "The Film Detective", "RetroPlex East",
        "Sundance", "MGM+ UHD", "LIFETIME MOVIES HD",
    ],
    # Broadcast locals — one reliable affiliate per network, plus Orlando locals
    "USA Local - FOX": [
        "FOX 5 WNYW NEW YORK",
        "FOX 35 WOFL ORLANDO",
    ],
    "USA Local - ABC": [
        "ABC 7 WABC NEW YORK",
        "ABC 9 WFTV ORLANDO",
    ],
    "USA Local - CBS": [
        "CBS 2 WFMY GREENSBORO",
        "CBS 6 WKMG ORLANDO",
    ],
    "USA Local - NBC": [
        "NBC 2 WESH ORLANDO",
    ],
    "USA Family & Kids": [
        "Cartoon Network East", "Disney Channel East", "Disney Junior East",
        "Disney XD", "Nick Jr East", "Nickelodeon UHD",
        "Boomerang", "PBS Kids", "Teen Nick", "Nick Toon UHD",
    ],
}

def fetch(action):
    url = f"{HOST}/player_api.php?username={USERNAME}&password={PASSWORD}&action={action}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)

def matches_curated(name, keywords):
    name_lower = name.lower().rstrip("*").strip()
    for kw in keywords:
        if kw.lower().rstrip("*").strip() in name_lower:
            return True
    return False

print("Fetching live categories...")
categories = fetch("get_live_categories")
cat_map = {c["category_id"]: c["category_name"] for c in categories}
print(f"  {len(categories)} categories")

print("Fetching live streams...")
streams = fetch("get_live_streams")
print(f"  {len(streams)} total live streams")

kept = 0
skipped = 0

print(f"Writing curated M3U to {OUTPUT}...")
tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(OUTPUT), suffix=".m3u.tmp")
with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    for s in streams:
        name = s.get("name", "")
        stream_id = s.get("stream_id", "")
        logo = s.get("stream_icon", "")
        for ov_key, ov_url in LOGO_OVERRIDES.items():
            if ov_key.lower() in name.lower():
                logo = ov_url
                break
        epg_id = s.get("epg_channel_id", "")
        if not epg_id:
            for ov_key, ov_id in EPG_ID_OVERRIDES.items():
                if ov_key.lower() in name.lower():
                    epg_id = ov_id
                    break
        cat_id = str(s.get("category_id", ""))
        group = cat_map.get(cat_id, "Uncategorized")

        include = False
        if group in FULL_GROUPS:
            include = True
        elif group in CURATED_GROUPS:
            include = matches_curated(name, CURATED_GROUPS[group])

        if include:
            url = f"{HOST}/{USERNAME}/{PASSWORD}/{stream_id}.ts"
            f.write(f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}\n')
            f.write(f"{url}\n")
            kept += 1
        else:
            skipped += 1

if kept < MIN_CHANNELS:
    os.unlink(tmp_path)
    raise SystemExit(
        f"ABORT: only {kept} channels matched (minimum {MIN_CHANNELS}). "
        f"Existing {OUTPUT} left untouched."
    )

os.chmod(tmp_path, 0o664)
os.replace(tmp_path, OUTPUT)

print(f"Done. {kept} channels kept, {skipped} skipped.")
