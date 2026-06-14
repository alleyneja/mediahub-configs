#!/usr/bin/env python3
"""
Generates a curated live-only M3U from the Xtream Codes API.
Run this script to refresh the channel list (cron refreshes daily at 4am).
"""
import json
import urllib.request

USERNAME = "alleyneja"
PASSWORD = "68fcMcytqb"
HOST = "http://link4tv.cc:80"
OUTPUT = "/srv/docker/threadfin/conf/live_only.m3u"

# Override broken/missing provider logos with stable ones (matched by name substring,
# case-insensitive). The provider's EPG lists a dead Bing search-thumbnail as CBS's
# primary icon (Threadfin picks the first <icon>, which 404s). Point it at the
# provider's OWN logo CDN (185.193.88.130) instead — same source Threadfin already
# caches successfully for every other channel. Requires x-update-channel-icon=True on
# the channel in xepg.json so Threadfin uses this M3U logo over the EPG icon.
LOGO_OVERRIDES = {
    "CBS 2 WFMY GREENSBORO": "http://185.193.88.130:80/images/d90cbfe9c7b343dd2eafef8117272284.png",
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
    # Broadcast locals — one reliable affiliate per network
    "USA Local - FOX": [
        "FOX 5 WNYW NEW YORK",
    ],
    "USA Local - ABC": [
        "ABC 7 WABC NEW YORK",
    ],
    "USA Local - CBS": [
        "CBS 2 WFMY GREENSBORO",
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
with open(OUTPUT, "w", encoding="utf-8") as f:
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

print(f"Done. {kept} channels kept, {skipped} skipped.")
