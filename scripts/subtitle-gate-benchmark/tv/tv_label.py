#!/usr/bin/env python3
"""TV benchmark step 1 (read-only): label known-good episodes. usage: tv_label.py OUT.json [PER_SHOW]
For each pilot show, picks PER_SHOW episodes spread across the run that have an English .srt and an embedded English text subtitle.
Signal 1: the file's cue starts agree with the embedded track (median nearest-start difference, and the share within 0.5 s).
Signal 2 (audio) is added later by the TV measurer. Writes [{show, ep, srt, video_c, embedded_agree...}] for later steps. Never writes to the library."""
import importlib.util, json, os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
spec = importlib.util.spec_from_file_location("rep", os.path.join(HERE, "..", "..", "subtitle-replace.py"))
rep = importlib.util.module_from_spec(spec); spec.loader.exec_module(rep)
SHOWS = ("Suits", "Hunter x Hunter (2011)")
def cues(path):
    t = open(path, encoding="utf-8", errors="replace").read(); o = []
    for m in re.finditer(r"(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)", t):
        g = list(map(int, m.groups())); o.append(g[0]*3600 + g[1]*60 + g[2] + g[3]/1e3)
    return o
def agree(a, b):
    """For each start in a, distance to the nearest start in b. Returns (median, share within 0.5 s)."""
    import bisect
    b = sorted(b); d = []
    for x in a:
        i = bisect.bisect_left(b, x); c = [abs(x - b[j]) for j in (i-1, i) if 0 <= j < len(b)]
        d.append(min(c))
    d.sort(); return d[len(d)//2], sum(1 for v in d if v <= 0.5) / len(d)
def embedded_index(video):
    o = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index,codec_name:stream_tags=language,title", "-of", "json", video], capture_output=True, text=True).stdout
    for x in json.loads(o or "{}").get("streams", []):
        t = {k.lower(): str(v) for k, v in (x.get("tags") or {}).items()}
        if t.get("language", "").lower() in ("eng", "en") and x.get("codec_name") in ("subrip", "ass", "ssa", "mov_text", "webvtt") \
                and not any(w in t.get("title", "").lower() for w in ("sign", "song", "forced", "commentary")):
            return x["index"]
    return None
def main():
    out = sys.argv[1]; per = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    db = rep.ro_db(); res = []
    for show in SHOWS:
        sid = db.execute("select sonarrSeriesId from table_shows where title=?", (show,)).fetchone()[0]
        eps = db.execute("select season,episode,path from table_episodes where sonarrSeriesId=? and episode_file_id is not null order by season,episode", (sid,)).fetchall()
        step = max(1, len(eps) // (per * 2)); tried = 0
        for season, ep, pc in eps[::step]:
            if sum(1 for r_ in res if r_["show"] == show) >= per: break
            host = rep.gate.host_path(pc); srt = os.path.splitext(host)[0] + ".en.srt"
            if not (os.path.isfile(host) and os.path.isfile(srt)): continue
            idx = embedded_index(host)
            if idx is None: continue
            with tempfile.TemporaryDirectory() as td:
                emb = os.path.join(td, "e.srt")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", host, "-map", f"0:{idx}", "-c:s", "srt", emb], capture_output=True)
                if not os.path.isfile(emb) or os.path.getsize(emb) < 200: continue
                a, b = cues(srt), cues(emb)
                if len(a) < 100 or len(b) < 100: continue
                med, share = agree(a, b)
            row = {"show": show, "season": season, "ep": ep, "srt": srt, "video_c": pc, "video": host, "embedded_index": idx,
                   "cues": len(a), "emb_cues": len(b), "median_diff_s": round(med, 3), "within_0.5s": round(share, 3),
                   "agrees": med <= 0.25 and share >= 0.9}
            res.append(row); print(show, f"S{season:02d}E{ep:02d}", "median", row["median_diff_s"], "within0.5", row["within_0.5s"], "AGREES" if row["agrees"] else "differs", flush=True)
    json.dump(res, open(out, "w"), indent=1)
    print(len(res), "episodes,", sum(r_["agrees"] for r_ in res), "agree with the embedded track")
main()
