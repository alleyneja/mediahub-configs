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

    # Batch 2: USA Entertainment + USA Movies Channels groups. Matched by
    # channel name against the DirecTV Orlando lineup's ~1015 stations; a few
    # channels (EPIX Drive-In, Lifetime Movies, RetroPlex, The Film Detective,
    # Showtime 2) had no equivalent in this lineup and were left unmapped —
    # they stay on the provider's own guide, unchanged.
    "aande.us": "10035",  # A&E
    "amc.us": "59337",  # AMC HD
    "animalplanet.us": "57394",  # Animal Planet HD
    "bbcamerica.us": "64492",  # BBC America HD
    "bether.us": "14897",  # BET Her
    "bet.us": "63236",  # BET HD
    "betwest.us": "14897",  # BET Her
    "bravo.us": "58625",  # Bravo HD
    "comedycentral.us": "62420",  # Comedy Central HD
    "discoverychannel.us": "56905",  # Discovery Channel HD
    "discoveryfamily.us": "67749",  # Discovery Family Channel HD
    "eentertainment.us": "61812",  # E! Entertainment Television HD
    "foodnetwork.us": "12574",  # Food Network
    "freeform.us": "59615",  # Freeform HD
    "fx.us": "58574",  # FX HD
    "fxx.us": "66379",  # FXX HD
    "gameshownetwork.us": "14909",  # Game Show Network
    "hallmarkmoviesmysteries.us": "46710",  # Hallmark Mystery HD (real-world rebrand)
    "hallmark.us": "66268",  # Hallmark Channel HD
    "hgtv.us": "14902",  # Home & Garden Television
    "historychannel.us": "57708",  # History HD
    "independentfilmchannel.us": "59444",  # IFC HD
    "investigationdiscovery.us": "65342",  # Investigation Discovery HD
    "lifetimenetwork.us": "60150",  # Lifetime HD
    "mtv.us": "10986",  # MTV - Music Television
    "natgeo.us": "49438",  # National Geographic HD
    "oxygen.us": "21484",  # Oxygen True Crime (real-world rebrand)
    "paramountnetwork.us": "59186",  # Paramount Network HD
    "syfy.us": "58623",  # Syfy HD
    "tlc.us": "11158",  # TLC
    "tnt.us": "42642",  # TNT HD
    "trutv.us": "64490",  # truTV HD
    "usanetwork.us": "11207",  # USA Network
    "vh1.us": "60046",  # VH1 HD
    "weatherchannel.us": "58812",  # The Weather Channel HD
    "cinemax.us": "34933",  # Cinemax HD
    "fxm.us": "14988",  # FXM
    "hbo2.us": "10241",  # HBO Hits
    "hbocomedypacific.us": "59839",  # HBO Comedy HD
    "hbocomedy.us": "59839",  # HBO Comedy HD
    "hbosignature.us": "10243",  # HBO Drama
    "hbo.us": "10240",  # HBO
    "hbowest.us": "10240",  # HBO
    "moremax.us": "10121",  # Cinemax Hits
    "showtimeextreme.us": "60947",  # Showtime Extreme HD
    "showtimeshowcase.us": "61001",  # Showtime Showcase HD
    "starzcinema.us": "67236",  # Starz Cinema HD
    "starzencore.us": "36225",  # Starz Encore HD
    "starzinblack.us": "67235",  # Starz in Black HD
    "sundancetv.us": "71280",  # SundanceTV HD
    "turnerclassicmovies.us": "12852",  # Turner Classic Movies
    "mgm.us": "65687",  # MGM+ HD (real-world EPIX rebrand)
    "mgmhits.us": "67929",  # MGM+ Hits HD
    "mgmmarquee.us": "74073",  # MGM+ Marquee HD
    "epixhits.us": "67929",  # MGM+ Hits HD (provider's duplicate "EPIX Hits" tvg-id)

    # Batch 3: Family & Kids, News, remaining Sports, and the 3 non-Orlando
    # local backups. Channels with no equivalent in this lineup (PBS Kids,
    # WFMY Greensboro, Al Jazeera, BBC World News, OAN, Scripps News, C-SPAN 3,
    # Fight Network, MavTV, Olympic Channel, PAC-12 Network, Stadium) were left
    # unmapped — they stay on the provider's own guide, unchanged.
    "boomerang.us": "21883",  # Boomerang
    "disneyjunior.us": "74885",  # Disney Junior HD
    "disneyxd.us": "60006",  # Disney XD HD
    "nickelodeon.us": "59432",  # Nickelodeon HD
    "nickjr.us": "82649",  # Nick Jr HD
    "teennick.us": "59036",  # Teen Nick
    "cartoonnetwork.us": "60048",  # Cartoon Network HD
    "disneychannel.us": "59684",  # Disney Channel HD
    "nicktoons.us": "30420",  # Nicktoons
    "abcwabc.us": "20453",  # WABC-DT (ABC 7 New York)
    "foxwnyw.us": "20360",  # WNYW-DT (FOX 5 New York)
    "golfchannel.us": "14899",  # Golf Channel
    "360northanchorage.us": "14899",  # Golf Channel (provider's mislabeled tvg-id, same real channel)
    "bloombergtv.us": "71799",  # Bloomberg HD
    "cnbc.us": "58780",  # CNBC HD
    "cnninternational.us": "10146",  # CNN International
    "cnn.us": "58646",  # CNN HD
    "cspan.us": "10161",  # CSPAN
    "cspan2.us": "10162",  # CSPAN2
    "foxbusiness.us": "58718",  # Fox Business HD
    "foxnews.us": "60179",  # Fox News Channel HD
    "hln.us": "64549",  # HLN HD
    "newsmax.us": "97163",  # Newsmax TV HD
    "newsnation.us": "91096",  # NewsNation
    "bigtennetwork.us": "58321",  # Big Ten Network HD
    "cbssportsnetworkusa.us": "59250",  # CBS Sports Network HD
    "foxdeportes.us": "72189",  # Fox Deportes HD
    "foxsports1.us": "82547",  # FS1 HD
    "foxsports2.us": "59305",  # FS2 HD
    "nflredzone.us": "65025",  # NFL RedZone HD
    "nhlnetwork.us": "58690",  # NHL Network HD
    "tennischannel.us": "33395",  # Tennis Channel
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
            base_title = titles[0].get("title120", "Unknown") if titles else "Unknown"
            subtitle = details.get("episodeTitle150", "")

            # Plex's Live TV grouping dedupes programmes across DIFFERENT channels
            # when their <title> strings match exactly, on the assumption that's
            # the same broadcast simulcast elsewhere. Generic sports titles like
            # "College Football" collide across every channel airing a game at
            # the same time, so Plex conflates unrelated games into one shared
            # metadata record and the "losers" show as Unknown Airing. Folding
            # the matchup into the title itself keeps it unique per game so Plex
            # can't merge two different games together.
            if subtitle:
                title = f"{base_title}: {subtitle}"
            else:
                title = base_title

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
