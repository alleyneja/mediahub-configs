#!/usr/bin/env python3
"""Post-run duration audit for lidarr-ytdl (Lidarr-YouTube-Downloader).

The downloader's album-first matching never rechecks duration, so a YouTube
edition that differs from MusicBrainz can put a wrong cut into the library
(issue #80). This compares every file the downloader created against Lidarr's
expected track length. Anything off by more than --tolerance seconds is deleted
through Lidarr's trackfile API (the track returns to Wanted) and the
downloader's own endpoint (which bans that YouTube URL for the track so it is
not re-grabbed), and a note goes to #media-alerts. Lidarr is never rescanned.

What counts as bad is decided by decide(): see its docstring. Reviewed-and-kept tracks
are reported but never touched.

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
import urllib.error
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


SIDECARS = (".lrc", ".txt")
DOWNLOADER_NAME = re.compile(r"^\d+ - ")  # what the downloader writes: "NN - Title"


def rename_downloader_files(album_id, artist_id, lh, apply, only_downloader_names=True):
    """Give the downloader's "NN - Title" files Lidarr's own naming (and album folder).

    The downloader names files and folders its own way (no "Artist - Album -" prefix, colons
    dropped instead of " - "), unlike a Lidarr download. Lidarr's rename preview is the source
    of truth for the target path, so this asks it rather than re-implementing the pattern.
    Only files that still carry the downloader's name are touched. Lidarr leaves sidecars and
    cover art behind, so they are moved to the audio's new folder, and the old folder is
    removed once empty. Never raises: a failed rename is retried by the bulk script
    (lidarr-rename-with-sidecars.py).
    """
    try:
        preview = [x for x in http("GET", f"{LIDARR}/rename?artistId={artist_id}&albumId={album_id}", lh) or []
                   if not only_downloader_names or DOWNLOADER_NAME.match(os.path.basename(x["existingPath"]))]
        if not preview:
            return 0
        for x in preview:
            print(("RENAME " if apply else "WOULD RENAME ") + x["existingPath"] + " -> " + x["newPath"])
        if not apply:
            return len(preview)
        cmd = http("POST", f"{LIDARR}/command", lh, {"name": "RenameFiles", "artistId": artist_id,
                                                      "files": [x["trackFileId"] for x in preview]})
        for _ in range(60):  # may queue behind a library-wide rescan
            st = http("GET", f"{LIDARR}/command/{cmd['id']}", lh)
            if st["status"] in ("completed", "failed"):
                break
            time.sleep(5)
        else:
            print(f"rename for album {album_id} still queued; later run will retry", file=sys.stderr)
            return 0
        if st["status"] != "completed":
            print(f"rename for album {album_id} failed: {st.get('exception', '')[:200]}", file=sys.stderr)
            return 0
        host = lambda p: p.replace(PATH_MAP[0], PATH_MAP[1], 1)
        olddirs = set()
        for x in preview:
            sd, dd = os.path.dirname(host(x["existingPath"])), os.path.dirname(host(x["newPath"]))
            olddirs.add((sd, dd))
            old, new = os.path.splitext(os.path.basename(x["existingPath"]))[0], \
                os.path.splitext(os.path.basename(x["newPath"]))[0]
            for ext in SIDECARS:
                s, t = os.path.join(sd, old + ext), os.path.join(dd, new + ext)
                if os.path.exists(s) and not os.path.exists(t):
                    os.rename(s, t)
        for sd, dd in olddirs:
            if sd == dd or not os.path.isdir(sd):
                continue
            for leftover in os.listdir(sd):  # cover.jpg and the like
                src, dst = os.path.join(sd, leftover), os.path.join(dd, leftover)
                if os.path.isfile(src) and not os.path.exists(dst) and not DOWNLOADER_NAME.match(leftover):
                    os.rename(src, dst)
            try:
                os.rmdir(sd)
            except OSError:
                pass  # still holds something else; leave it
        return len(preview)
    except Exception as e:  # never break the audit over cosmetics
        print(f"rename step skipped for album {album_id}: {e}", file=sys.stderr)
        return 0


VANISHED_STATE = os.path.expanduser("~/logs/lidarr-ytdl-vanished.json")
VANISHED_EVERY = 86400  # the check costs one Lidarr request per downloaded album


def stranded_downloads(folder):
    """Downloader-named audio files still sitting in `folder` (host path)."""
    try:
        return sorted(f for f in os.listdir(folder)
                      if DOWNLOADER_NAME.match(f) and f.lower().endswith((".mp3", ".flac", ".m4a", ".opus", ".ogg")))
    except OSError:
        return []


def report_vanished_albums(db, lh):
    """Flag downloaded albums that Lidarr has since dropped, leaving untracked files behind.

    MusicBrainz sometimes merges a deluxe/anniversary release group into the standard
    album; Lidarr then deletes the extra album (its id starts returning 404) and files the
    downloader wrote for it are orphaned: invisible to Lidarr, duplicating tracks that are
    already owned. Nothing noticed this for weeks (Nov 23 2026: 13 albums). This reports
    each one once, only while downloader-named files are still in its folder (a renamed
    or re-imported album is fine). Reports only; what to do with the files is a human call.
    """
    try:
        with open(VANISHED_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    if time.time() - st.get("last", 0) < VANISHED_EVERY:
        return
    reported = st.setdefault("reported", {})
    rows = db.execute("SELECT album_id, MAX(artist_name), MAX(album_title), MAX(lidarr_album_path) "
                      "FROM track_downloads WHERE success=1 AND deleted=0 AND lidarr_album_path != '' "
                      "GROUP BY album_id").fetchall()
    found, seen_dirs = [], set()
    for aid, artist, title, path in rows:
        try:
            http("GET", f"{LIDARR}/album/{aid}", lh)
            continue
        except urllib.error.HTTPError as e:
            if e.code != 404:
                continue
        host = path.replace("/music", PATH_MAP[1], 1)
        files = stranded_downloads(host)
        if files and str(aid) not in reported and host not in seen_dirs:
            seen_dirs.add(host)
            found.append((aid, f"{artist} - {title}: {len(files)} untracked file(s) in {os.path.relpath(host, PATH_MAP[1])}"))
    for aid, _ in found:
        reported[str(aid)] = int(time.time())
    st["last"] = time.time()
    os.makedirs(os.path.dirname(VANISHED_STATE), exist_ok=True)
    with open(VANISHED_STATE, "w") as f:
        json.dump(st, f)
    for _, line in found:
        print("VANISHED " + line)
    if found:
        discord("lidarr-ytdl: %d downloaded album(s) no longer exist in Lidarr but their files are still in the "
                "library, untracked (usually MusicBrainz merged an edition into another album; they likely "
                "duplicate tracks you own). Review, import or remove:\n%s"
                % (len(found), "\n".join("- " + x for _, x in found)))


VERIFIED_SCORE = 0.90   # AcoustID score that counts as "this is the right song"
GROSS_FRACTION = 0.25   # deviation from MusicBrainz big enough to mean a different song


def decide(actual, mb, yt, ac, tol=10.0, identity_ok=True, last_resort=False):
    """Judge one file. Returns (verdict, reason); verdict is ok|delete|review|unverifiable.

    Only two things justify a delete: a file shorter than the YouTube source it came
    from (truncated download; AcoustID only fingerprints the start so it cannot see
    this), or a file grossly off MusicBrainz that AcoustID did not verify (wrong song).
    Everything else that merely differs from MusicBrainz is an alternate edition or
    padding, so it is reported but kept. A file the downloader deliberately kept as a
    documented last resort (a shorter cut or another recording of the same song) is
    never deleted here: that would only ban it and have it fetched again.
    """
    verdict, why = _decide(actual, mb, yt, ac, tol, identity_ok)
    if last_resort and verdict == "delete":
        return "review", f"downloader kept this as a last resort ({why})"
    return verdict, why


def _decide(actual, mb, yt, ac, tol, identity_ok):
    if not identity_ok:
        return "unverifiable", "linked file is not the one the downloader wrote"
    if mb > 0 and abs(actual - mb) <= tol:
        return "ok", ""
    if yt > 0 and actual < yt - tol:
        return "delete", f"truncated: file is {actual:.0f}s but its YouTube source was {yt:.0f}s"
    if mb <= 0 and ac >= VERIFIED_SCORE:
        return "ok", ""  # right song per AcoustID, MusicBrainz has no length to disagree with
    if mb <= 0:
        # MusicBrainz has no length for this track: unknown, not "100% off". Without a
        # reference there is nothing to judge against, so keep the file and report it
        # (deleting it only re-queues the track and loops the download every 72h).
        return "review", "MusicBrainz has no length for this track; kept, nothing to compare against"
    rel = abs(actual - mb) / mb
    if rel > GROSS_FRACTION and ac < VERIFIED_SCORE:
        return "delete", f"{rel:.0%} off MusicBrainz and AcoustID did not verify it"
    if ac >= VERIFIED_SCORE:
        return "review", f"AcoustID verified the song; length differs from MusicBrainz ({rel:.0%})"
    return "review", f"length differs from MusicBrainz by {rel:.0%}, unverified"


def track_file_number(track, tracks):
    """The number the downloader used in this track's file name (its processing.py
    _track_number): absoluteTrackNumber when that is album-wide, otherwise, on a
    multi-disc release whose absolute numbers repeat per disc, the (disc, track) position."""
    absolute = [int(t.get("absoluteTrackNumber") or 0) for t in tracks]
    multi = len({int(t.get("mediumNumber") or 1) for t in tracks}) > 1
    if not multi or (len(set(absolute)) == len(absolute) and min(absolute) > 0):
        return int(track.get("absoluteTrackNumber") or 0)
    ordered = sorted(tracks, key=lambda t: (int(t.get("mediumNumber") or 1), int(t.get("trackNumber") or 0)))
    return next(i + 1 for i, t in enumerate(ordered) if t["id"] == track["id"])


def is_downloaders_file(path, row, lidarr_track_number=None):
    """The Lidarr-linked file must be the one this row's download produced: right track
    number in its name, and written after the download (not an older file)."""
    base = os.path.basename(path)
    num = f"{row['track_number']:02d} - "
    # the downloader writes "NN - Title"; after Lidarr's rename it is "Artist - Album - NN - Title"
    # on a multi-disc release Lidarr's rename uses the per-disc number, not the downloader's
    alt = f" - {lidarr_track_number:02d} - " if lidarr_track_number else None
    if not (base.startswith(num) or f" - {num}" in base or (alt and alt in base)):
        return False
    try:
        return os.path.getmtime(path) >= row["timestamp"] - 120
    except OSError:
        return False


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
    ap.add_argument("--no-delete", action="store_true",
                    help="with --apply: still link files and advance the cursor, but only report bad tracks")
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

    last_resort_ids = {x[0] for x in db.execute(
        "SELECT DISTINCT track_download_id FROM candidate_attempts "
        "WHERE outcome='accepted_unverified_fallback' AND error_message LIKE 'last resort:%'")}
    lh = {"X-Api-Key": lidarr_key()}
    artist_of = {}
    for aid in sorted({k[0] for k in latest}):
        try:
            artist_of[aid] = http("GET", f"{LIDARR}/album/{aid}", lh)["artistId"]
        except urllib.error.HTTPError:
            continue
        folder = next(r["lidarr_album_path"] for k, r in latest.items() if k[0] == aid)
        if folder:
            try:
                link_unlinked(aid, folder.replace("/music", PATH_MAP[0], 1), lh, a.apply)
                rename_downloader_files(aid, artist_of[aid], lh, a.apply)
            except urllib.error.HTTPError as e:  # album re-keyed/removed by a Lidarr refresh
                print(f"link step skipped for album {aid}: HTTP {e.code}", file=sys.stderr)
    by_album = {}
    checked = bad = pending = 0
    reports, reviews, pending_ts = [], [], []
    for (album_id, tno), r in sorted(latest.items()):
        if album_id not in by_album:
            try:
                tracks = http("GET", f"{LIDARR}/track?albumId={album_id}", lh)
                files = {f["id"]: f for f in http("GET", f"{LIDARR}/trackfile?albumId={album_id}", lh)}
            except urllib.error.HTTPError as e:
                print(f"album {album_id} not in Lidarr (HTTP {e.code}); skipped", file=sys.stderr)
                tracks, files = [], {}
            by_album[album_id] = (tracks, files)
        tracks, files = by_album[album_id]
        if not tracks:
            continue
        t = next((x for x in tracks if x.get("trackFileId") and track_file_number(x, tracks) == tno), None)
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
        verdict, why = decide(actual, expected, float(r["duration_seconds"] or 0),
                              float(r["acoustid_score"] or 0), a.tolerance,
                              is_downloaders_file(path, r, int(t.get("trackNumber") or 0)), r["id"] in last_resort_ids)
        if verdict == "unverifiable":
            print(f"unverifiable {r['artist_name']} - {r['track_title']}: {why} ({os.path.basename(path)})")
            pending += 1  # may be a rescan still settling; retried until PENDING_EXPIRY
            pending_ts.append(r["timestamp"])
            continue
        checked += 1
        if verdict == "ok":
            continue
        line = (f"{r['artist_name']} - {r['album_title']} #{tno} {r['track_title']}: "
                f"{actual:.0f}s vs MB {expected:.0f}s, YT {r['duration_seconds']}s, "
                f"AcoustID {float(r['acoustid_score'] or 0):.2f} -> {why}")
        if verdict == "review":
            reviews.append(line)
            print("REVIEW " + line)
            continue
        bad += 1
        reports.append(line)
        deleting = a.apply and not a.no_delete
        print(("DELETE " if deleting else "WOULD DELETE ") + line)
        if deleting:
            # Lidarr first: drops the record and the file, track returns to Wanted.
            # Never RescanFolders: it un-links the downloader's other, thinly tagged files.
            http("DELETE", f"{LIDARR}/trackfile/{f['id']}", lh)
            try:  # the downloader's own delete records the removal and bans the URL
                http("DELETE", f"{DOWNLOADER}/api/download/track/{r['id']}", body={"ban_url": True})
            except Exception as e:
                print(f"downloader ban failed: {e}", file=sys.stderr)

    print(f"checked {checked}, bad {bad}, review {len(reviews)}, pending {pending}, tolerance {a.tolerance}s")
    if a.apply:
        report_vanished_albums(db, lh)
        # Never advance past a row we could not judge yet.
        # A row that never shows up in Lidarr stops holding the cursor after an hour.
        recent = [t for t in pending_ts if t > time.time() - PENDING_EXPIRY]
        save_cursor(min(recent) - 1 if recent else cutoff)
        if reports:
            if a.no_delete:
                discord("lidarr-ytdl audit flagged %d wrong-length track(s) (NOT deleted, "
                        "review mode):\n%s" % (bad, "\n".join("- " + x for x in reports)))
            else:
                discord("lidarr-ytdl audit removed %d wrong-length track(s); they stay on Wanted "
                        "and the YouTube URL is banned for that track:\n%s"
                        % (bad, "\n".join("- " + x for x in reports)))
        if reviews:
            discord("lidarr-ytdl audit kept %d track(s) that differ from MusicBrainz "
                    "(alternate edition or unverified, nothing deleted):\n%s"
                    % (len(reviews), "\n".join("- " + x for x in reviews)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
