#!/usr/bin/env python3
"""Look tracks up on LRCLIB and write lyrics next to FLAC/MP3 files.

Default mode writes sidecar files: <track>.lrc (timed) or <track>.txt (plain),
which is the only form Plex reads (embedded tags are not supported by Plex, see
support.plex.tv/articles/215916117). --embed writes the lyrics into the tags
instead (for players that read them); it verifies an audio-stream md5 before and
after and restores the file from a copy on any difference.

Dry run unless --apply. Tracks that already have the target (sidecar present, or
embedded lyrics in --embed mode) are skipped. Prefers synced lyrics.

usage: lrclib-embed-lyrics.py [--apply] [--embed] [--min-age 3600] DIR [DIR ...]
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
FAIL_STREAK = 5  # consecutive failed lookups before cooling down
COOLDOWN = 300  # seconds to wait when LRCLIB looks unhealthy
MAX_COOLDOWNS = 3  # stop the run after this many cooldowns in a row with no success
MISS_TTL = 30 * 86400  # don't re-ask about a track LRCLIB had nothing for within this long
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


def _norm(t):
    return "".join(c for c in (t or "").lower().replace("\u2019", "'") if c.isalnum())


def lookup(artist, title, album, duration):
    """Best LRCLIB entry. If the exact match has no synced lyrics, other entries
    for the same song (same title, length within tolerance) are checked for them."""
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title,
                                "album_name": album, "duration": round(duration)})
    hit = get(f"{API}/get?{q}")
    time.sleep(PACE)
    how = "exact" if hit else None
    if hit and (hit.get("syncedLyrics") or hit.get("instrumental")):
        return hit, how
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    cands = [c for c in (get(f"{API}/search?{q}") or [])
             if c.get("duration") and abs(c["duration"] - duration) <= DURATION_TOL
             and _norm(c.get("trackName")) == _norm(title)]
    time.sleep(PACE)
    for c in cands:
        if c.get("syncedLyrics"):
            return c, "search" if not hit else "search-synced"
    if hit:
        return hit, how
    for c in cands:
        if c.get("plainLyrics") or c.get("instrumental"):
            return c, "search"
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
    ap.add_argument("--upgrade", action="store_true",
                    help="retry tracks that only have a .txt sidecar; replace it if timed lyrics now exist")
    ap.add_argument("--embed", action="store_true", help="write into tags instead of sidecar files")
    ap.add_argument("--min-age", type=int, default=3600, help="skip files modified more recently than this (s)")
    ap.add_argument("--backup-dir", help="copy each file here before writing")
    ap.add_argument("--state", default=os.path.expanduser("~/logs/lrclib-misses.json"),
                    help="remembers tracks with no lyrics so they are not re-asked for 30 days")
    ap.add_argument("--plex", help="Plex base URL; folders that got new files are rescanned at the end")
    a = ap.parse_args()

    try:
        with open(a.state) as fh:
            misses = json.load(fh)
    except (OSError, ValueError):
        misses = {}
    streak = cooldowns = 0
    touched = set()

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
                base = os.path.splitext(p)[0]
                if not a.embed:
                    has = os.path.exists(base + ".lrc") or (
                        os.path.exists(base + ".txt") and not a.upgrade)
                if has:
                    stats["skipped_has"] += 1
                    continue
                if time.time() - misses.get(p, 0) < MISS_TTL and not a.upgrade:
                    stats["skipped_known_miss"] = stats.get("skipped_known_miss", 0) + 1
                    continue
                stats["checked"] += 1
                try:
                    hit, how = lookup(artist, title, album, dur)
                except Exception as e:
                    stats["failed"] += 1
                    print(f"ERROR     {fn}: lookup failed ({e}); will retry next run")
                    streak += 1
                    if streak >= FAIL_STREAK:
                        cooldowns += 1
                        if cooldowns >= MAX_COOLDOWNS:
                            print(f"STOPPING  LRCLIB unhealthy ({cooldowns} cooldowns in a row); rerun later")
                            return finish(a, stats, misses, touched, 3)
                        print(f"COOLDOWN  {streak} failures in a row, waiting {COOLDOWN}s ({cooldowns}/{MAX_COOLDOWNS})")
                        time.sleep(COOLDOWN)
                        streak = 0
                    continue
                streak = cooldowns = 0
                if not hit:
                    stats["missing"] += 1
                    misses[p] = time.time()
                    print(f"MISSING   {fn}")
                    continue
                if hit.get("instrumental"):
                    stats["instrumental"] += 1
                    misses[p] = time.time()
                    print(f"INSTRUMENTAL {fn}")
                    continue
                text, kind = (hit.get("syncedLyrics"), "synced") if hit.get("syncedLyrics") \
                    else (hit.get("plainLyrics"), "plain")
                if not text:
                    stats["missing"] += 1
                    misses[p] = time.time()
                    print(f"MISSING   {fn}")
                    continue
                stats["found_" + kind] += 1
                print(f"{kind.upper():9} {fn}  [{how}, lrclib '{hit.get('trackName')}' {hit.get('duration')}s vs {dur:.0f}s]")
                if not a.apply:
                    continue
                if not a.embed:
                    if a.upgrade and kind != "synced":
                        continue  # nothing better than the .txt we already have
                    side = base + (".lrc" if kind == "synced" else ".txt")
                    with open(side + ".tmp", "w", encoding="utf-8") as fh:
                        fh.write(text.replace("\r\n", "\n"))
                    os.replace(side + ".tmp", side)
                    if kind == "synced" and os.path.exists(base + ".txt"):
                        os.remove(base + ".txt")
                    stats["written"] += 1
                    touched.add(root)
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
                    touched.add(root)
                except Exception as e:  # restore on any problem
                    shutil.copy2(tmp, p)
                    os.remove(tmp)
                    stats["failed"] += 1
                    print(f"FAILED    {fn}: {e} (restored)")
    return finish(a, stats, misses, touched, 0)


def finish(a, stats, misses, touched, code):
    if a.apply:
        try:
            os.makedirs(os.path.dirname(a.state), exist_ok=True)
            with open(a.state + ".tmp", "w") as fh:
                json.dump(misses, fh)
            os.replace(a.state + ".tmp", a.state)
        except OSError as e:
            print(f"could not save {a.state}: {e}")
        if a.plex:
            for d in sorted(touched):
                path = d.replace("/mnt/media", "/data/media", 1)
                try:
                    urllib.request.urlopen(f"{a.plex}/library/sections/3/refresh?force=1&path="
                                           + urllib.parse.quote(path), timeout=30).read()
                except Exception as e:
                    print(f"plex refresh failed for {d}: {e}")
                time.sleep(2)
    print(json.dumps(stats))
    return code


if __name__ == "__main__":
    sys.exit(main())
