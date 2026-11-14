#!/usr/bin/env python3
"""Rename YouTube-downloaded tracks to Lidarr's naming pattern, carrying lyric sidecars along.

Why: lidarr-ytdl writes `NN - Title.ext`; Lidarr's pattern is
`Artist - Album - NN - Title.ext`. Lidarr's RenameFiles command renames the audio
only, so `.lrc`/`.txt` sidecars (see lrclib-embed-lyrics.py) would be orphaned.
This asks Lidarr for its own rename preview (the source of truth for new names),
runs RenameFiles for exactly those track files, then renames the sidecars to match.

Scope: albums that appear in the downloader's track_downloads table
(success=1, deleted=0). Nothing else is touched. Plex keeps its track IDs across
a rename (pilot Nov 13 2026: The Script - Science & Faith), only paths change.

Dry run by default. --apply renames. --album-id N limits to one album.
NOTE: the first bulk run (Nov 13) left sidecars behind for multi-disc / renamed-folder
albums (Lidarr moves audio there); fixed by matching sidecars to audio afterwards, and
this script now targets the new folder. Idempotent: albums with an empty rename preview are skipped; a sidecar is moved
only if its target does not already exist.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

DB = "/srv/docker/lidarr-ytdl/config/lidarr-downloader.db"
LIDARR = "http://localhost:8686/api/v1"
LIDARR_CFG = "/srv/docker/lidarr/config.xml"
PATH_MAP = ("/data/music", "/mnt/media/music")
SIDECARS = (".lrc", ".txt")


def key():
    with open(LIDARR_CFG) as f:
        return re.search(r"<ApiKey>([^<]+)</ApiKey>", f.read()).group(1)


def call(method, path, k, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        LIDARR + "/" + path, data=data, method=method,
        headers={"X-Api-Key": k, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def host(p):
    return p.replace(PATH_MAP[0], PATH_MAP[1], 1)


def stem(p):
    return os.path.splitext(os.path.basename(p))[0]


def sidecar_moves(preview):
    """[(src, dst)] for sidecars whose audio is being renamed."""
    moves = []
    for x in preview:
        # Lidarr may also move audio to a new folder (multi-disc "CD 01/", or a
        # renamed album folder), so the sidecar goes to the audio's NEW folder.
        sd = os.path.dirname(host(x["existingPath"]))
        dd = os.path.dirname(host(x["newPath"]))
        old, new = stem(x["existingPath"]), stem(x["newPath"])
        for ext in SIDECARS:
            s, t = os.path.join(sd, old + ext), os.path.join(dd, new + ext)
            if os.path.exists(s) and not os.path.exists(t):
                moves.append((s, t))
    return moves


def wait(k, cid):
    for _ in range(90):
        s = call("GET", f"command/{cid}", k)
        if s["status"] in ("completed", "failed"):
            return s
        time.sleep(2)
    return {"status": "timeout"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--album-id", type=int)
    ap.add_argument("--log", default=os.path.expanduser("~/logs/lidarr-rename-with-sidecars.log"))
    a = ap.parse_args()
    k = key()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ids = [r[0] for r in db.execute(
        "select distinct album_id from track_downloads where success=1 and deleted=0 order by album_id")]
    if a.album_id:
        ids = [a.album_id]
    tot = {"albums": 0, "files": 0, "sidecars": 0, "errors": 0, "skipped_clean": 0}
    refresh = []
    log = open(a.log, "a")
    for aid in ids:
        try:
            al = call("GET", f"album/{aid}", k)
            prev = call("GET", f"rename?artistId={al['artistId']}&albumId={aid}", k)
        except Exception as e:
            tot["errors"] += 1
            print(f"ERROR album {aid}: {e}")
            continue
        if not prev:
            tot["skipped_clean"] += 1
            continue
        moves = sidecar_moves(prev)
        tot["albums"] += 1
        tot["files"] += len(prev)
        tot["sidecars"] += len(moves)
        print(f"{'APPLY' if a.apply else 'DRY  '} {al['artist']['artistName']} - {al['title']}: "
              f"{len(prev)} audio, {len(moves)} sidecars")
        if not a.apply:
            continue
        s = wait(k, call("POST", "command", k, {
            "name": "RenameFiles", "artistId": al["artistId"],
            "files": [x["trackFileId"] for x in prev]})["id"])
        if s["status"] != "completed":
            tot["errors"] += 1
            print(f"  ERROR rename {s}")
            continue
        done = 0
        for src, dst in moves:
            if os.path.exists(dst) or not os.path.exists(src):
                continue
            os.rename(src, dst)
            done += 1
        log.write(json.dumps({"album": aid, "audio": len(prev), "sidecars": done,
                              "ts": time.time()}) + "\n")
        log.flush()
        refresh.append(os.path.dirname(os.path.dirname(host(prev[0]["newPath"]))) if False else os.path.dirname(host(prev[0]["newPath"])))
    print(json.dumps(tot))
    if a.apply and refresh:
        with open(os.path.expanduser("~/logs/lidarr-rename-dirs.txt"), "a") as f:
            f.write("\n".join(refresh) + "\n")
        print(f"{len(refresh)} folders need a Plex rescan (listed in ~/logs/lidarr-rename-dirs.txt)")


if __name__ == "__main__":
    sys.exit(main())
