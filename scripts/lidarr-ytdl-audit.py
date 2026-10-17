#!/usr/bin/env python3
"""Post-run duration audit for lidarr-ytdl (Lidarr-YouTube-Downloader).

The downloader's album-first matching never rechecks duration, so a YouTube
edition that differs from MusicBrainz can put a wrong cut into the library
(issue #80). This compares every file the downloader created against Lidarr's
expected track length. Anything off by more than --tolerance seconds is deleted
through Lidarr's trackfile API (the track returns to Wanted) and the
downloader's own endpoint (which bans that YouTube URL for the track so it is
not re-grabbed), and a note goes to #media-alerts. Lidarr is never rescanned.

Scope: only rows in the downloader's track_downloads table (success=1,
deleted=0). Nothing else in the library is ever examined or touched.

Default is a dry run. --apply deletes and advances the cursor.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = "/srv/docker/lidarr-ytdl/config/lidarr-downloader.db"
LIDARR = "http://localhost:8686/api/v1"
LIDARR_CFG = "/srv/docker/lidarr/config.xml"
DOWNLOADER = "http://localhost:5005"
WEBHOOK_ENV = os.path.join(REPO, "scripts/lidarr-ytdl-audit.env")  # gitignored, #media-alerts
STATE = os.path.expanduser("~/logs/lidarr-ytdl-audit.json")
PATH_MAP = ("/data/music", "/mnt/media/music")  # Lidarr container -> host
PENDING_EXPIRY = 3600
SETTLE_SECONDS = 300  # let Lidarr's ~90s import finish before judging a row


def http(method, url, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": "lidarr-ytdl-audit/1.0"}  # Discord 403s urllib's default UA
    h.update(headers or {})
    if data is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def lidarr_key():
    with open(LIDARR_CFG) as f:
        return re.search(r"<ApiKey>([^<]+)</ApiKey>", f.read()).group(1)


def link_unlinked(album_id, folder, lh, apply):
    """Import files Lidarr left on disk but unlinked ("Has Missing Tracks").

    The downloader copies an album into the library and calls RefreshArtist; Lidarr
    then rejects partial albums, so the files sit unlinked. ManualImport with explicit
    trackIds bypasses that gate. Only files Lidarr matched to exactly one track of
    this album are imported; anything else is left alone.
    """
    qs = urllib.parse.urlencode({"folder": folder, "filterExistingFiles": "true",
                                 "replaceExistingFiles": "false"})
    items = http("GET", f"{LIDARR}/manualimport?{qs}", lh) or []
    linked = {f["path"] for f in http("GET", f"{LIDARR}/trackfile?albumId={album_id}", lh)}
    files = []
    for it in items:
        if it["path"] in linked:  # the endpoint's own filterExistingFiles does not exclude these
            continue
        alb, trk = it.get("album"), it.get("tracks") or []
        if not alb or alb["id"] != album_id or len(trk) != 1:
            continue
        files.append({"path": it["path"], "artistId": it["artist"]["id"], "albumId": album_id,
                      "albumReleaseId": it["albumReleaseId"], "trackIds": [trk[0]["id"]],
                      "quality": it["quality"], "indexerFlags": it.get("indexerFlags", 0),
                      "disableReleaseSwitching": True})
    for f in files:
        print(("LINK " if apply else "WOULD LINK ") + f["path"])
    if files and apply:
        http("POST", f"{LIDARR}/command", lh,
             {"name": "ManualImport", "files": files, "importMode": "move"})
        time.sleep(15)
    return len(files)


def probe_seconds(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=60)
    return float(out.stdout.strip())


def discord(msg):
    url = None
    try:
        with open(WEBHOOK_ENV) as f:
            for line in f:
                if line.startswith("DISCORD_WEBHOOK_URL="):
                    url = line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    if not url:
        print("no webhook, not posting:", msg, file=sys.stderr)
        return
    http("POST", url, body={"content": msg[:1900]})


def load_cursor():
    try:
        with open(STATE) as f:
            return json.load(f)["cursor"]
    except (OSError, ValueError, KeyError):
        return time.time() - 24 * 3600  # first run: last 24h only


def save_cursor(ts):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({"cursor": ts}, f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tolerance", type=float, default=10.0)
    ap.add_argument("--since", type=float, help="epoch override for the cursor")
    ap.add_argument("--album-id", type=int)
    ap.add_argument("--no-settle", action="store_true", help="skip the age gate (testing)")
    a = ap.parse_args()

    status = http("GET", f"{DOWNLOADER}/api/download/status")
    if status.get("active") and not a.no_settle:
        print("downloader busy, skipping")
        return 0

    cursor = a.since if a.since is not None else load_cursor()
    cutoff = time.time() - (0 if a.no_settle else SETTLE_SECONDS)
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    q = ("SELECT * FROM track_downloads WHERE success=1 AND deleted=0 "
         "AND timestamp>? AND timestamp<=?")
    args = [cursor, cutoff]
    if a.album_id is not None:
        q += " AND album_id=?"
        args.append(a.album_id)
    rows = db.execute(q + " ORDER BY timestamp", args).fetchall()
    latest = {}  # newest row per (album, track number): earlier ones were retries
    for r in rows:
        latest[(r["album_id"], r["track_number"])] = r

    lh = {"X-Api-Key": lidarr_key()}
    for aid in sorted({k[0] for k in latest}):
        folder = next(r["lidarr_album_path"] for k, r in latest.items() if k[0] == aid)
        if folder:
            link_unlinked(aid, folder.replace("/music", PATH_MAP[0], 1), lh, a.apply)
    by_album = {}
    checked = bad = pending = 0
    reports, pending_ts = [], []
    for (album_id, tno), r in sorted(latest.items()):
        if album_id not in by_album:
            tracks = http("GET", f"{LIDARR}/track?albumId={album_id}", lh)
            files = {f["id"]: f for f in http("GET", f"{LIDARR}/trackfile?albumId={album_id}", lh)}
            by_album[album_id] = (tracks, files)
        tracks, files = by_album[album_id]
        t = next((x for x in tracks if x["absoluteTrackNumber"] == tno and x.get("trackFileId")), None)
        f = files.get(t["trackFileId"]) if t else None
        if not f:
            pending += 1
            pending_ts.append(r["timestamp"])
            print(f"pending (not in Lidarr yet): {r['artist_name']} - {r['track_title']}")
            continue
        path = f["path"].replace(PATH_MAP[0], PATH_MAP[1], 1)
        try:
            actual = probe_seconds(path)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            print(f"cannot probe {path}: {e}", file=sys.stderr)
            pending += 1
            pending_ts.append(r["timestamp"])
            continue
        expected = t["duration"] / 1000.0
        checked += 1
        diff = actual - expected
        if abs(diff) <= a.tolerance:
            continue
        bad += 1
        line = (f"{r['artist_name']} - {r['album_title']} #{tno} {r['track_title']}: "
                f"{actual:.0f}s vs expected {expected:.0f}s ({diff:+.0f}s)")
        reports.append(line)
        print(("DELETE " if a.apply else "WOULD DELETE ") + line)
        if a.apply:
            # Lidarr first: drops the record and the file, track returns to Wanted.
            # Never RescanFolders: it un-links the downloader's other, thinly tagged files.
            http("DELETE", f"{LIDARR}/trackfile/{f['id']}", lh)
            try:  # the downloader's own delete records the removal and bans the URL
                http("DELETE", f"{DOWNLOADER}/api/download/track/{r['id']}", body={"ban_url": True})
            except Exception as e:
                print(f"downloader ban failed: {e}", file=sys.stderr)

    print(f"checked {checked}, bad {bad}, pending {pending}, tolerance {a.tolerance}s")
    if a.apply:
        # Never advance past a row we could not judge yet.
        # A row that never shows up in Lidarr stops holding the cursor after an hour.
        recent = [t for t in pending_ts if t > time.time() - PENDING_EXPIRY]
        save_cursor(min(recent) - 1 if recent else cutoff)
        if reports:
            discord("lidarr-ytdl audit removed %d wrong-length track(s); they stay on Wanted "
                    "and the YouTube URL is banned for that track:\n%s"
                    % (bad, "\n".join("- " + x for x in reports)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
