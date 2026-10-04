#!/usr/bin/env python3
"""Survey which Live TV channels carry usable closed captions (issue #66).

Reads the curated M3U, samples SECONDS of each stream, and records:
  - cc_flag: ffprobe reports closed_captions on the video stream
  - cc_chars / readable_pct: text decoded by ffmpeg's EIA-608 decoder
Read-only. One Xtreme connection at a time; pauses while anything is connected to
Threadfin (:34400) so it never competes with a viewer.

usage: cc-survey.py [--limit N] [--out FILE.csv]
"""
import argparse, csv, json, os, re, subprocess, sys, tempfile, time

M3U = "/srv/docker/threadfin/conf/live_only.m3u"
SECONDS = 20


def channels():
    name = group = tvg = None
    for line in open(M3U, encoding="utf-8"):
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = re.search(r'tvg-id="([^"]*)".*?group-title="([^"]*)",(.*)$', line)
            tvg, group, name = (m.group(1), m.group(2), m.group(3)) if m else ("", "", line)
        elif line and not line.startswith("#"):
            yield name, group, tvg, line


def viewers():
    out = subprocess.run(["ss", "-tn", "state", "established", "( sport = :34400 )"],
                         capture_output=True, text=True).stdout
    return max(0, len(out.strip().splitlines()) - 1)


def sample(url, path):
    subprocess.run(["curl", "-sL", "-m", str(SECONDS), "-o", path, url], capture_output=True)
    return os.path.getsize(path) if os.path.exists(path) else 0


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
                       capture_output=True, text=True)
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None, ""
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return None, ""
    return v.get("closed_captions", 0), f'{v.get("codec_name")} {v.get("width")}x{v.get("height")}'


def captions(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"movie={path}[out0+subcc]",
                        "-map", "0:s:0", "-t", str(SECONDS), "-f", "srt", "-"],
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in r.stdout.splitlines()
             if l.strip() and not l.strip().isdigit() and "-->" not in l]
    text = " ".join(re.sub(r"\{[^}]*\}|<[^>]*>|\\[hN]", "", l) for l in lines)
    return text


def readable(text):
    letters = sum(c.isalpha() or c == " " for c in text)
    return round(100 * letters / len(text)) if text else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default=os.path.expanduser("~/logs/cc-survey/survey-%s.csv" % time.strftime("%Y%m%d-%H%M")))
    a = ap.parse_args()
    fields = ["name", "group", "tvg_id", "bytes", "video", "cc_flag", "cc_chars", "readable_pct", "sample"]
    with open(a.out, "w", newline="") as f, tempfile.TemporaryDirectory() as td:
        w = csv.DictWriter(f, fields); w.writeheader()
        for i, (name, group, tvg, url) in enumerate(channels()):
            if a.limit and i >= a.limit:
                break
            while viewers():
                print("viewer connected, pausing 60s", flush=True); time.sleep(60)
            p = os.path.join(td, "s.ts")
            n = sample(url, p)
            row = dict(name=name, group=group, tvg_id=tvg, bytes=n, video="", cc_flag="", cc_chars=0, readable_pct=0, sample="")
            if n > 100000:
                flag, vid = probe(p)
                row.update(video=vid, cc_flag=flag)
                try:
                    t = captions(p)
                except subprocess.TimeoutExpired:
                    t = ""
                row.update(cc_chars=len(t), readable_pct=readable(t), sample=t[:80])
            w.writerow(row); f.flush()
            print(i, name, row["cc_flag"], row["cc_chars"], row["readable_pct"], flush=True)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
