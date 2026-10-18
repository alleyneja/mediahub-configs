#!/usr/bin/env python3
"""Look tracks up on LRCLIB and embed lyrics into FLAC/MP3 tags.

Dry run by default; --apply writes. Tags only: the audio stream is never
touched, and each write is verified by comparing an audio-stream md5 before and
after (the file is restored from a copy if it differs). Tracks that already have
lyrics are skipped. Prefers synced (LRC) lyrics, falls back to plain.

usage: lrclib-embed-lyrics.py [--apply] [--min-age 3600] DIR [DIR ...]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT
from mutagen.mp3 import MP3

API = "https://lrclib.net/api"
UA = "mediahub-lyrics-embed/1.0 (https://github.com/alleyneja/mediahub-configs)"
DURATION_TOL = 3.0  # seconds between our file and the LRCLIB entry
PACE = 1.0  # seconds between requests
EXTS = (".flac", ".mp3")


def get(url):
    """JSON or None on 404; retries 429/5xx and network errors with backoff."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
        time.sleep(5 * 2 ** attempt)


def lookup(artist, title, album, duration):
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title,
                                "album_name": album, "duration": round(duration)})
    hit = get(f"{API}/get?{q}")
    time.sleep(PACE)
    if hit:
        return hit, "exact"
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    for cand in get(f"{API}/search?{q}") or []:
        if cand.get("duration") and abs(cand["duration"] - duration) <= DURATION_TOL \
                and (cand.get("syncedLyrics") or cand.get("plainLyrics") or cand.get("instrumental")):
            time.sleep(PACE)
            return cand, "search"
    time.sleep(PACE)
    return None, None


def audio_md5(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:a", "-c", "copy", "-f", "md5", "-"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def tags_of(path):
    """(artist, title, album, duration, has_lyrics)"""
    m = mutagen.File(path, easy=False)
    if path.lower().endswith(".flac"):
        t = m.tags or {}
        first = lambda k: (t.get(k) or [""])[0]
        return (first("artist") or first("albumartist"), first("title"), first("album"),
                m.info.length, bool(first("lyrics") or first("unsyncedlyrics")))
    t = m.tags or {}
    txt = lambda k: str(t[k].text[0]) if k in t else ""
    return (txt("TPE1") or txt("TPE2"), txt("TIT2"), txt("TALB"), m.info.length,
            any(k.startswith("USLT") for k in t.keys()))


def write_lyrics(path, text):
    if path.lower().endswith(".flac"):
        f = FLAC(path)
        f["LYRICS"] = text
        f.save()
    else:
        try:
            id3 = ID3(path)
        except mutagen.id3.ID3NoHeaderError:
            id3 = ID3()
        id3.add(USLT(encoding=3, lang="eng", desc="", text=text))
        id3.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--min-age", type=int, default=3600, help="skip files modified more recently than this (s)")
    ap.add_argument("--backup-dir", help="copy each file here before writing")
    a = ap.parse_args()

    stats = dict(checked=0, skipped_has=0, skipped_new=0, found_synced=0, found_plain=0,
                 instrumental=0, missing=0, written=0, failed=0)
    for d in a.dirs:
        for root, _, files in os.walk(d):
            for fn in sorted(files):
                if not fn.lower().endswith(EXTS):
                    continue
                p = os.path.join(root, fn)
                if time.time() - os.path.getmtime(p) < a.min_age:
                    stats["skipped_new"] += 1
                    continue
                artist, title, album, dur, has = tags_of(p)
                if has:
                    stats["skipped_has"] += 1
                    continue
                stats["checked"] += 1
                try:
                    hit, how = lookup(artist, title, album, dur)
                except Exception as e:
                    stats["failed"] += 1
                    print(f"ERROR     {fn}: lookup failed ({e}); will retry next run")
                    continue
                if not hit:
                    stats["missing"] += 1
                    print(f"MISSING   {fn}")
                    continue
                if hit.get("instrumental"):
                    stats["instrumental"] += 1
                    print(f"INSTRUMENTAL {fn}")
                    continue
                text, kind = (hit.get("syncedLyrics"), "synced") if hit.get("syncedLyrics") \
                    else (hit.get("plainLyrics"), "plain")
                if not text:
                    stats["missing"] += 1
                    print(f"MISSING   {fn}")
                    continue
                stats["found_" + kind] += 1
                print(f"{kind.upper():9} {fn}  [{how}, lrclib '{hit.get('trackName')}' {hit.get('duration')}s vs {dur:.0f}s]")
                if not a.apply:
                    continue
                before = audio_md5(p)
                tmp = p + ".lyrics-bak"
                shutil.copy2(p, tmp)
                try:
                    write_lyrics(p, text)
                    if before is None or audio_md5(p) != before:
                        raise RuntimeError("audio md5 changed or unreadable")
                    if a.backup_dir:
                        os.makedirs(a.backup_dir, exist_ok=True)
                        shutil.copy2(tmp, os.path.join(a.backup_dir, fn))
                    os.remove(tmp)
                    stats["written"] += 1
                except Exception as e:  # restore on any problem
                    shutil.copy2(tmp, p)
                    os.remove(tmp)
                    stats["failed"] += 1
                    print(f"FAILED    {fn}: {e} (restored)")
    print(json.dumps(stats))


if __name__ == "__main__":
    sys.exit(main())
