#!/usr/bin/env python3
"""
Generates a curated live-only M3U for the Digitalizard IPTV provider (second
provider, added 2026-11-27 as a head-to-head test against Xtreme HD IPTV).

Why this exists: the provider's own m3u_plus URL is ~464 MB / 1.47M entries
(98% VOD movies + series episodes). Pointing Threadfin at it directly pins the
container at 2+ cores / 4 GB RAM indefinitely while it tries to parse it. Same
fix as generate_live_m3u.py for Xtreme: pull the live list from the Xtream API
and write only the channels we want.

The lineup deliberately mirrors the core of the Xtreme lineup (sports, news,
locals, entertainment) so the two providers can be compared channel-for-channel.

Gotcha: the provider resets TCP connections for curl/python-urllib default
user-agents. Requests must send a player-ish UA.
"""
import json
import os
import tempfile
import urllib.request


def _load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env_file(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".env"))

USERNAME = os.environ["DL_USERNAME"]
PASSWORD = os.environ["DL_PASSWORD"]
HOST = "http://zfiqyzhr.lzrdns3.com"
OUTPUT = "/srv/docker/threadfin/conf/dl_live.m3u"
USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"
NAME_PREFIX = "DL "  # makes these channels distinguishable from Xtreme's in Threadfin/Plex

# Refuse to overwrite a working lineup with an empty/truncated one.
MIN_CHANNELS = 40

# The provider gives these no epg_channel_id; borrow the id Xtreme uses.
EPG_ID_OVERRIDES = {
    "NBC 02 (WESH) ORLANDO": "nbcwesh.us",
}

# Exact channel names (after stripping the "US| " prefix and surrounding
# whitespace), keyed by provider group. Exact matching avoids pulling in the
# [BK] backup / 4K / [WEST] siblings.
WANTED = {
    "AM | USA ESPN": [
        "ESPN 1 HD", "ESPN 2 HD", "ESPN NEWS HD", "ESPN SEC NETWORK HD", "ESPN U HD",
    ],
    "AM | USA FOX SPORT": [
        "FOX SPORTS 1 HD", "FOX SPORTS 2 HD", "FOX DEPORTES",
    ],
    "AM | USA SPORTS": [
        "NFL NETWORK", "NFL REDZONE  HD", "GOLF CHANNEL HD", "TENNIS CHANNEL HD",
        "ACC NETWORK HD", "BIG TEN NETWORK HD", "CBS SPORTS NETWORK HD",
        "CBS SPORTS GOLAZO NETWORK HD", "FIGHT NETWORK  HD", "MAV TV",
        "NHL NETWORK HD", "NBA TV FHD", "MLB NETWORK HD", "STADIUM HD",
    ],
    "AM | USA NEWS": [
        "FOX NEWS HD", "FOX BUSINESS HD", "CNN FHD", "CNN INTERNATIONAL FHD",
        "MSNBC HD", "CNBC HD", "THE WEATHER CHANNEL HD", "HLN HD",
        "BBC WORLD NEWS HD", "NEWSMAX HD", "NEWS NATION HD (EAST)", "BLOOMBERG HD",
        "AL JAZEERA HD", "ONE AMERICAN NEWS NETWORK HD", "C-SPAN 1 HD",
        "C-SPAN 2 HD", "C-SPAN 3 HD", "BBC AMERICA HD (EAST)",
    ],
    "AM | USA ABC": ["ABC 07 (WABC) NEW YORK", "ABC 09 (WFTV) ORLANDO"],
    "AM | USA CBS": ["CBS 02 (WFMY) GREENSBORO", "CBS 06 (WKMG) ORLANDO"],
    "AM | USA FOX": ["FOX 05 (WNYW) NEW YORK", "FOX 35 (WOFL) ORLANDO"],
    "AM | USA NBC": ["NBC 02 (WESH) ORLANDO", "NBC 04 (WNBC) NEW YORK"],
    "AM | USA ENTERTAINMENT": [
        "A&E FHD", "COMEDY CENTRAL EAST HD", "E! ENTERTAINMENT EAST HD",
        "FOOD NETWORK HD (EAST)", "FREEFORM HD [EAST]", "GAME SHOW NETWORK HD",
        "HALLMARK CHANNEL HD", "HGTV EAST HD", "LIFETIME HD", "MTV HD", "TLC EAST",
        "TRU TV (East) FHD", "USA NETWORK EAST HD", "VH1 EAST",
    ],
    "AM | USA GENERAL": ["BRAVO (EAST)"],
    "AM | USA CINEMA": [
        "AMC HD", "BET (EAST)", "FX HD", "FXM HD", "IFC HD", "SYFY East HD",
        "TNT EAST FHD", "TBS EAST HD", "TCM HD", "PARAMOUNT NETWORK HD (EAST)",
        "HBO EAST HD", "HBO COMEDY EAST FHD", "HBO HITS [EAST]", "SHOWTIME EAST HD",
        "CINEMAX EAST FHD", "STARZ EAST FHD", "STARZ CINEMA FHD", "SUNDANCE HD",
        "HALLMARK MOVIES & MYSTERIES HD", "LIFETIME MOVIE FHD", "THE FILM DETECTIVE HD",
    ],
    "AM | USA KIDS&FAMLIY": [
        "CARTOON NETWORK EAST HD", "DISNEY CHANNEL HD (EAST)", "DISNEY JUNIOR (EAST) HD",
        "DISNEY XD (EAST) HD", "NICK JR EAST HD", "NICKELODEON EAST HD",
        "BOOMERANG HD", "PBS KIDS HD", "TEENNICK EAST HD", "NICKTOONS HD EAST",
    ],
}


def clean(name):
    name = name.strip()
    if name.startswith("US|"):
        name = name[3:]
    return name.strip()


def fetch(action):
    url = f"{HOST}/player_api.php?username={USERNAME}&password={PASSWORD}&action={action}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


print("Fetching live categories...")
cat_map = {c["category_id"]: c["category_name"].strip() for c in fetch("get_live_categories")}
print("Fetching live streams...")
streams = fetch("get_live_streams")
print(f"  {len(streams)} total live streams")

wanted = {g.strip(): set(names) for g, names in WANTED.items()}
found = set()
lines = []
for s in streams:
    group = cat_map.get(str(s.get("category_id", "")), "")
    name = clean(s.get("name", ""))
    if name not in wanted.get(group, ()) or (group, name) in found:
        continue
    found.add((group, name))
    # Namespace the tvg-id. Threadfin refreshes stream URLs by tvg-id across
    # ALL providers, so a DL channel sharing "espn.us" with Xtreme's ESPN
    # overwrote Xtreme's URL: on 2026-11-27, 92 Xtreme channels silently
    # streamed from Digitalizard. Guide data for DL channels comes from the
    # x-mapping stored in xepg.json, not from this id.
    epg_id = s.get("epg_channel_id") or EPG_ID_OVERRIDES.get(name, "")
    epg_id = f"dl.{epg_id}" if epg_id else ""
    display = NAME_PREFIX + name
    lines.append(
        f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{display}" tvg-logo="{s.get("stream_icon", "")}" '
        f'group-title="Digitalizard",{display}\n'
        f'{HOST}/live/{USERNAME}/{PASSWORD}/{s["stream_id"]}.ts\n'
    )

missing = sorted(f"{g} / {n}" for g, names in wanted.items() for n in names if (g, n) not in found)
for m in missing:
    print(f"  MISSING: {m}")

if len(lines) < MIN_CHANNELS:
    raise SystemExit(f"ABORT: only {len(lines)} channels matched (minimum {MIN_CHANNELS}). {OUTPUT} left untouched.")

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUTPUT), suffix=".m3u.tmp")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write("#EXTM3U\n")
    f.writelines(lines)
os.chmod(tmp, 0o664)
os.replace(tmp, OUTPUT)
print(f"Done. {len(lines)} channels written to {OUTPUT}, {len(missing)} missing.")
