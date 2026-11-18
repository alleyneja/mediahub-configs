#!/usr/bin/env python3
"""Subtitle replace tool for TV EPISODES (issue #73, docs/subtitle-tv-mode.md). Run by hand; not cron, not wired into the wrapper.

  subtitle-replace-tv.py --plan    "Suits S04E02" ...   measure the current English sub with rule tv-v1.1, show its tier and candidates; changes nothing
  subtitle-replace-tv.py --run     "Suits S04E02" ...   replace (tiered, same rules as movies)
  subtitle-replace-tv.py --restore "Suits S04E02"       put the newest backup back

Same safety rules as subtitle-replace.py (movies): Tier A (last cue before 50% of the runtime, or under 20 cues) is backed up, blacklisted
and removed before candidates are tried; Tier B is backed up, a candidate is let in, measured, and the backup is PUT BACK unless the
candidate passes. UNVERIFIABLE is never a target. Spanish files and global Bazarr settings are never touched. No PATCH search.
Differences from movies: the TV rule (6 x 180 s windows, tv-v1.1, plus an overlapping-cue check), the Bazarr episode endpoints, the Plex
guard stops only while the SHOW being worked on is being watched, no direct subsource path (no IMDb id per episode), and subsource
candidates are tried after opensubtitles and subdl (it failed 56% of episodes in the 2026-11-17 survey). Whisperai is the last resort.
Episodes with no Bazarr download on record (an older or hand-added file) are measured from the file on disk; nothing is blacklisted for them.
"""
import argparse, datetime, fcntl, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rep", os.path.join(HERE, "subtitle-replace.py"))
rep = importlib.util.module_from_spec(spec); spec.loader.exec_module(rep)
gate = rep.gate
log, ledger, api, ro_db = rep.log, rep.ledger, rep.api, rep.ro_db

MEASURER = os.path.join(HERE, "subtitle-gate-benchmark", "tv", "measure_tv.py")
TV = {"cutoff": 10.0, "min_cues": 10, "windows": 6, "min_usable": 5, "max_off": 0.75, "spread": 1.0, "coverage": 0.9, "overlap_share": 0.05}
MAX_DISTINCT_FAILS = 8      # after this many different subtitle texts have failed, stop working through the rest and try whisperai
CURRENT_SHOW = None
CURRENT_VIDEO = None
THIS_SHOW_MSG = "STOP: this show is being watched"
EPISODE_QUERY = gate.PLEX_QUERY_TMPL % (
    'v=[x for x in m.get(\\"Metadata\\",[]) if x.get(\\"type\\")==\\"episode\\" and x.get(\\"live\\")!=\\"1\\" '
    'and not str(x.get(\\"key\\",\\"\\")).startswith(\\"/livetv\\")]; '
    'print(json.dumps({\\"shows\\":[x.get(\\"grandparentTitle\\",\\"\\") for x in v],'
    '\\"files\\":[p.get(\\"file\\",\\"\\").split(\\"/\\")[-1] for x in v for md in x.get(\\"Media\\",[]) for p in md.get(\\"Part\\",[])],'
    '\\"episodes\\":len(v),\\"all\\":m.get(\\"size\\",0)}))')


# ---------------------------------------------------------------- rule tv-v1.1
def overlap_share(text):
    """Share of cues that start more than 0.3 s before the previous cue ends (two tracks merged into one file)."""
    c = [(int(a) * 3600 + int(b) * 60 + int(s) + int(ms) / 1e3, int(a2) * 3600 + int(b2) * 60 + int(s2) + int(ms2) / 1e3)
         for a, b, s, ms, a2, b2, s2, ms2 in re.findall(r"(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)", text)]
    return sum(1 for i in range(1, len(c)) if c[i][0] < c[i - 1][1] - 0.3) / max(len(c), 1)


def judge_tv(rec, overlap=0.0, p=None):
    """(state, [reasons]). PASS / FAIL (positive evidence) / UNVERIFIABLE. Pure; tested in test_subtitle_replace_tv.py."""
    p = p or TV
    ws = [w for w in rec["windows"] if w and w["cues"] >= p["min_cues"]]
    offs = [w["offset"] for w in ws]
    cov = rec["last_cue_end"] / rec["dur"] if rec.get("dur") else 0
    why = []
    if overlap >= p["overlap_share"]:
        why.append(f"{overlap:.0%} of cues overlap the previous one (two subtitle tracks merged in one file)")
    if len(ws) < p["windows"]:
        why.append(f"only {len(ws)} of {p['windows']} windows have enough cues (missing dialogue or truncated)")
    if cov < p["coverage"]:
        why.append(f"last cue ends at {cov * 100:.0f}% of the runtime")
    if why:
        return "FAIL", why

    def check(offs):
        use = [o for o in offs if abs(o) <= p["cutoff"]]
        big = [o for o in offs if abs(o) > p["cutoff"]]
        if use and max(abs(o) for o in use) > p["max_off"]:
            return "FAIL", [f"a usable window reads {max(use, key=abs):+.1f} s"], use
        if use and max(use) - min(use) > p["spread"]:
            return "FAIL", [f"usable windows disagree by {max(use) - min(use):.1f} s (drift or a different cut)"], use
        for b in big:
            if sum(1 for o in big if abs(o - b) <= 2.0) >= 3:
                return "FAIL", [f"3 or more windows agree on a large offset (~{b:+.0f} s)"], use
        return "OK", [], use

    s, why, use = check(offs)
    if s == "FAIL":
        # one stray window is allowed if the other five pass on their own and no two of them agree on a big offset
        rest = list(offs); rest.remove(max(offs, key=abs))
        s2, _, use2 = check(rest)
        if s2 == "OK" and len(use2) == len(rest) == p["windows"] - 1 >= p["min_usable"]:
            return "PASS", []
        return "FAIL", why
    if len(use) < p["min_usable"]:
        return "UNVERIFIABLE", [f"only {len(use)} usable windows; nothing contradicts the subtitle"]
    return "PASS", []


def measure_tv(items):
    """items: [{"sub_c","video_c","sub"}]. Returns {sub host path: record}. Runs inside the bazarr container at nice 19, one worker."""
    work = tempfile.mkdtemp(prefix="tvgate-")
    jobs = [{"title": f"j{i}", "variant": "live", "srt": it["sub_c"], "video": it["video_c"]} for i, it in enumerate(items)]
    json.dump(jobs, open(f"{work}/jobs.json", "w"))
    try:
        for src, dst in ((MEASURER, "/tmp/measure_tv.py"), (f"{work}/jobs.json", "/tmp/tvgate_jobs.json")):
            subprocess.run(["docker", "cp", src, f"{gate.CONTAINER}:{dst}"], check=True, capture_output=True)
        subprocess.run(["docker", "exec", gate.CONTAINER, "rm", "-f", "/tmp/tvgate_out.jsonl"], check=True, capture_output=True)
        subprocess.run(["docker", "exec", "-e", "PYTHONPATH=/app/bazarr/bin/libs", "-e", "TV_CFG=A", gate.CONTAINER, "nice", "-n", "19", "python3",
                        "/tmp/measure_tv.py", "/tmp/tvgate_jobs.json", "/tmp/tvgate_out.jsonl", "1"], check=True, capture_output=True, text=True, timeout=3600)
        subprocess.run(["docker", "cp", f"{gate.CONTAINER}:/tmp/tvgate_out.jsonl", f"{work}/out.jsonl"], check=True, capture_output=True)
        res = {json.loads(l)["title"]: json.loads(l) for l in open(f"{work}/out.jsonl")}
        return {items[int(k[1:])]["sub"]: v for k, v in res.items()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def measure_one(sub_c, video_c):
    item = {"sub_c": sub_c, "video_c": video_c, "sub": gate.host_path(sub_c)}
    rec = measure_tv([item]).get(item["sub"])
    if not rec:
        return None, None, ["no measurement"]
    try:
        ov = overlap_share(open(item["sub"], encoding="utf-8", errors="replace").read())
    except OSError:
        ov = 0.0
    st, why = judge_tv(rec, ov)
    return rec, st, why


def measure_text(text, video_c):
    """Measure a subtitle that is not in the library (a shifted candidate): copy to the container's /tmp, measure, remove."""
    name = f"tvc_{os.getpid()}_{int(time.time() * 1000) % 10**9}.srt"
    with tempfile.TemporaryDirectory() as td:
        host = os.path.join(td, name)
        open(host, "w", encoding="utf-8").write(text)
        subprocess.run(["docker", "cp", host, f"{gate.CONTAINER}:/tmp/{name}"], check=True, capture_output=True)
    try:
        item = {"sub_c": f"/tmp/{name}", "video_c": video_c, "sub": f"/tmp/{name}"}
        rec = measure_tv([item]).get(item["sub"])
        if not rec:
            return None, None, ["no measurement"]
        st, why = judge_tv(rec, overlap_share(text))
        return rec, st, why
    finally:
        subprocess.run(["docker", "exec", gate.CONTAINER, "rm", "-f", f"/tmp/{name}"], capture_output=True)


def shifted_pass(text, rec, video_c):
    """If rec is a clean constant shift, correct it in memory and measure again. Tries the measured offset, then its opposite (the sign
    convention is not assumed). Returns (text, delta, state, why, rec2) for the last try, or None when the shift is not constant."""
    off = rep.shift_plan(rec)
    if off is None:
        return None
    last = None
    for delta in (off, -off):
        txt = rep.shift_srt(text, delta)
        rec2, st, why = measure_text(txt, video_c)
        last = (txt, delta, st, why, rec2)
        if st == "PASS":
            return last
    return last


# ---------------------------------------------------------------- guard: only the show being watched blocks
def norm_show(s):
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\s*\(\d{4}\)\s*$", "", (s or "").lower()))


def guard_decision(d, show, video, allow=False):
    """None = go ahead, else the STOP message. Pure. Episodes of other shows, movies and live TV never block."""
    if allow or show is None:
        return None
    if video in d["files"] or any(norm_show(s) == norm_show(show) for s in d["shows"]):
        return THIS_SHOW_MSG + "; nothing further done on it"
    if d["episodes"] > len(d["files"]) or any(not s for s in d["shows"]):
        return "STOP: an episode stream could not be identified; nothing further done"
    return None


def guard():
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jay@192.168.0.22", EPISODE_QUERY],
                           capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout)
        if isinstance(d.get("shows"), list) and isinstance(d.get("files"), list) and isinstance(d.get("episodes"), int):
            msg = guard_decision(d, CURRENT_SHOW, CURRENT_VIDEO)
            if msg:
                sys.exit(msg)
            return
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass
    if subprocess.run(["ping", "-c1", "-W2", "192.168.0.22"], capture_output=True).returncode == 0:
        sys.exit("STOP: Plex stream count unknown; nothing further done")


# ---------------------------------------------------------------- episodes in Bazarr
def find_episode(text):
    mt = re.match(r"^(.*?)\s+S(\d+)E(\d+)$", text.strip(), re.I)
    if not mt:
        sys.exit(f"'{text}': use the form 'Show Name S04E02'")
    show, s, e = mt.group(1), int(mt.group(2)), int(mt.group(3))
    db = ro_db()
    rows = db.execute("select sonarrSeriesId,title from table_shows where title like ?", (f"%{show}%",)).fetchall()
    exact = [r for r in rows if norm_show(r[1]) == norm_show(show)] or rows
    if len(exact) != 1:
        sys.exit(f"'{show}' matches {len(exact)} shows: {[r[1] for r in exact]}")
    sid, stitle = exact[0]
    ep = db.execute("select sonarrEpisodeId,path from table_episodes where sonarrSeriesId=? and season=? and episode=?", (sid, s, e)).fetchone()
    if not ep:
        sys.exit(f"{stitle} S{s:02d}E{e:02d}: no such episode")
    eid, path_c = ep
    title = f"{stitle} S{s:02d}E{e:02d}"
    h = db.execute("select id,provider,subs_id,language,subtitles_path from table_history where sonarrEpisodeId=? "
                   "and action in (1,2,3) and language in ('en','en:hi') order by id desc limit 1", (eid,)).fetchone()
    video = gate.host_path(path_c)
    base = os.path.splitext(video)[0]
    if h and os.path.exists(gate.host_path(h[4])):
        sub_c, lang = h[4], h[3]
    else:                                              # no history row, or the recorded file is gone: take what is on disk
        found = [(f"{base}{ext}", lg) for ext, lg in ((".en.srt", "en"), (".en.hi.srt", "en:hi")) if os.path.isfile(f"{base}{ext}")]
        if not found:
            sys.exit(f"{title}: no English subtitle on disk")
        sub_c, lang = gate_c_path(found[0][0]), found[0][1]
        h = (None, None, None, lang, sub_c)
    return {"sid": sid, "eid": eid, "show": stitle, "title": title, "video_c": path_c, "video": video, "hist": h,
            "sub_c": sub_c, "sub": gate.host_path(sub_c), "lang": lang}


def gate_c_path(host):
    return rep.gate_c(host)


def candidates(m, tried):
    st, body = api("GET", "/providers/episodes", {"episodeid": m["eid"]}, 600)
    if st != 200:
        sys.exit(f"provider search failed: {st}")
    bl = {str(r[0]) for r in ro_db().execute("select subs_id from table_blacklist")}
    cur_hi = m["lang"].endswith(":hi")
    en = [c for c in json.loads(body)["data"] if c["language"].split(":")[0] == "en" and c["subtitle"] not in bl
          and (c["provider"], c["subtitle"]) not in tried and c["subtitle"] != m["hist"][2]]
    past = rep.ledger_tried(m["title"])
    en = [c for c in en if (c["provider"], str(c["release_info"]).lower(), str(c["uploader"])) not in past]
    en.sort(key=lambda c: (c["provider"] == "subsource", -c["orig_score"], c["hearing_impaired"] != cur_hi))
    seen, first, rest = set(), [], []
    for c in en:
        k = str(c["release_info"]).lower()
        (rest if k in seen else first).append(c)
        seen.add(k)
    en = first + rest
    return [c for c in en if c["provider"] != "whisperai"], [c for c in en if c["provider"] == "whisperai"]


def jobs_busy(m):
    st, body = api("GET", "/system/jobs", {}, 60)
    if st != 200:
        return 1
    return sum(1 for j in json.loads(body)["data"] if j["status"] in ("pending", "running")
               and (m["show"] in j["job_name"] or "Manually downloading" in j["job_name"]))


def drain(m, max_s=2400):
    t0 = time.time()
    while time.time() - t0 < max_s:
        if jobs_busy(m) == 0:
            return True
        time.sleep(15)
    return False


def download(m, c):
    """POST one candidate, wait for Bazarr's queue, return (status, container path of the new file or None, note)."""
    if not drain(m, 600):
        return 0, None, "Bazarr already busy with this show; not starting another job"
    t_post = time.time()
    before = ro_db().execute("select coalesce(max(id),0) from table_history").fetchone()[0]
    st, body = api("POST", "/providers/episodes", {"seriesid": m["sid"], "episodeid": m["eid"], "hi": str(c["hearing_impaired"]), "forced": "False",
                   "original_format": str(c["original_format"]), "provider": c["provider"], "subtitle": c["subtitle"]})
    if st not in (200, 204):
        return st, None, body[:160]
    time.sleep(5)
    if not drain(m):
        return st, None, "Bazarr jobs did not finish in time"
    time.sleep(5)
    row = ro_db().execute("select id,subtitles_path from table_history where sonarrEpisodeId=? and id>? and action in (1,2,3) "
                          "and language in ('en','en:hi') order by id desc limit 1", (m["eid"], before)).fetchone()
    if row:
        return st, row[1], ""
    d = os.path.dirname(m["sub"]); stem = os.path.basename(os.path.splitext(m["video"])[0])
    new = [f for f in os.listdir(d) if f.startswith(stem) and re.search(r"\.en(\.hi)?\.srt$", f) and os.stat(os.path.join(d, f)).st_mtime >= t_post - 2]
    if new:
        return st, gate_c_path(os.path.join(d, new[0])), ""
    return st, None, "no new English subtitle appeared"


def blacklist(m, provider, subs_id, path_c):
    if not provider or not subs_id:
        return 0, False                                # an old file with no history row: nothing to blacklist
    st, body = api("POST", "/episodes/blacklist", {"seriesid": m["sid"], "episodeid": m["eid"], "provider": provider, "subs_id": subs_id,
                                                   "language": "en", "subtitles_path": path_c})
    row = ro_db().execute("select count(*) from table_blacklist where subs_id=?", (str(subs_id),)).fetchone()[0]
    return st, bool(row)


def delete_sub(m, hi, path_c):
    return api("DELETE", "/episodes/subtitles", {"seriesid": m["sid"], "episodeid": m["eid"], "language": "en", "forced": "False",
                                                 "hi": str(hi), "path": path_c})


def run_episode(m, tier, rec):
    guard()
    bak = rep.backup(m)
    ledger({"title": m["title"], "kind": "episode", "event": "start", "tier": tier, "backup": bak})
    if tier == "A":
        st, ok = blacklist(m, m["hist"][1], m["hist"][2], m["sub_c"])
        log(f"Tier A: blacklisted current ({st}, row={ok}); file gone={not os.path.exists(m['sub'])}")
        if os.path.exists(m["sub"]):
            st, _ = delete_sub(m, m["lang"].endswith(":hi"), m["sub_c"])
            log(f"  deleted via API ({st}); gone={not os.path.exists(m['sub'])}")
    tried, tries, outcome, seen_text, distinct_fails = set(), 0, "unresolved", {}, 0
    normal, whisper = candidates(m, tried)
    log(f"{len(normal)} usable candidates + {len(whisper)} whisperai")
    queue = normal + whisper[:1]
    t_start = time.time()
    try:
        for c in queue:
            if c["provider"] != "whisperai" and time.time() - t_start > rep.NORMAL_BUDGET_S:
                log("  time budget for regular candidates used up; skipping to whisperai"); continue
            if c["provider"] != "whisperai" and distinct_fails >= MAX_DISTINCT_FAILS:
                if distinct_fails == MAX_DISTINCT_FAILS:
                    log(f"  {MAX_DISTINCT_FAILS} different subtitle texts have failed; skipping to whisperai"); distinct_fails += 1
                continue
            guard()
            tries += 1
            tried.add((c["provider"], c["subtitle"]))
            log(f"try {tries}: {c['provider']} score {c['orig_score']} hi={c['hearing_impaired']} | {str(c['release_info'])[:60]}")
            st, newpath_c, note = download(m, c)
            if st not in (200, 204) or not newpath_c:
                log(f"  download failed ({st}) {note}"); ledger({"title": m["title"], "kind": "episode", "event": "download_failed", "cand": c["subtitle"], "status": st})
                if tier == "B":
                    rep.put_back(bak, m["sub"])
                continue
            newpath = gate.host_path(newpath_c)
            if not rep.settle(newpath):
                log("  file never settled; restoring" if tier == "B" else "  file never settled")
            guard()
            digest = hashlib.sha1(open(newpath, "rb").read()).hexdigest() if os.path.exists(newpath) else None
            if digest in seen_text:                 # the same subtitle served under another release name or by another provider
                nrec, nst, why = seen_text[digest]
                log(f"  same text as an earlier candidate; not measured again -> {nst}")
            else:
                nrec, nst, why = measure_one(newpath_c, m["video_c"])
                if digest:
                    seen_text[digest] = (nrec, nst, why)
                if nst != "PASS":
                    distinct_fails += 1
            log(f"  candidate -> {nst} {why[:2]}")
            ledger({"title": m["title"], "kind": "episode", "event": "candidate", "provider": c["provider"], "subs_id": c["subtitle"],
                    "release": str(c["release_info"]).lower(), "uploader": str(c["uploader"]), "state": nst, "why": why, "path": newpath,
                    "offsets": [round(w["offset"], 2) if w else None for w in (nrec or {}).get("windows", [])]})
            if nst != "PASS" and nrec is not None and rep.shift_plan(nrec) is not None:
                try:
                    text = open(newpath, encoding="utf-8", errors="replace").read()
                    r = shifted_pass(text, nrec, m["video_c"])
                except OSError:
                    r = None
                if r:
                    stext, delta, nst, why, nrec = r
                    log(f"  shifted {delta:+.2f}s -> {nst} {why[:2]}")
                    ledger({"title": m["title"], "kind": "episode", "event": "candidate", "provider": c["provider"], "subs_id": c["subtitle"],
                            "release": str(c["release_info"]).lower(), "uploader": str(c["uploader"]), "state": nst, "why": why,
                            "path": newpath, "via": "shifted", "delta": delta})
                    if nst == "PASS":
                        tmp = newpath + ".shift.tmp"
                        open(tmp, "w", encoding="utf-8").write(stext)
                        shutil.copymode(bak, tmp)
                        os.replace(tmp, newpath)       # the candidate file in the library becomes the corrected text
            if nst == "PASS":
                if newpath != m["sub"] and tier == "B" and os.path.exists(m["sub"]):
                    os.remove(m["sub"])
                outcome = "replaced"; log(f"  ACCEPTED at {newpath}")
                break
            if tier == "B":
                if newpath == m["sub"]:
                    rep.put_back(bak, m["sub"])
                elif os.path.exists(newpath):
                    os.remove(newpath)
                bst, brow = blacklist(m, c["provider"], c["subtitle"], newpath_c + ".rejected")
            else:
                bst, brow = blacklist(m, c["provider"], c["subtitle"], newpath_c)
                if os.path.exists(newpath):
                    delete_sub(m, c["hearing_impaired"], newpath_c)
            log(f"  rejected; blacklist {bst} row={brow}; restored/removed={'yes' if tier == 'B' else 'removed'}")
    except BaseException:
        if tier == "B" and outcome != "replaced":
            rep.put_back(bak, m["sub"]); log("interrupted: Tier B snapshot restored")
            d = os.path.dirname(m["sub"]); stem = os.path.basename(os.path.splitext(m["video"])[0])
            for f in os.listdir(d):             # a candidate Bazarr wrote under another name (.en.srt vs .en.hi.srt) and was not yet vetted
                full = os.path.join(d, f)
                if f.startswith(stem) and re.search(r"\.en(\.hi)?\.srt$", f) and full != m["sub"] and os.stat(full).st_mtime >= t_start - 2:
                    os.remove(full); log(f"interrupted: removed unvetted candidate {f}")
        ledger({"title": m["title"], "kind": "episode", "event": "interrupted", "tier": tier, "outcome": outcome})
        raise
    ledger({"title": m["title"], "kind": "episode", "event": "end", "tier": tier, "outcome": outcome, "tries": tries})
    return outcome


def process(m, plan_only):
    global CURRENT_SHOW, CURRENT_VIDEO
    CURRENT_SHOW, CURRENT_VIDEO = m["show"], os.path.basename(m["video"])
    guard()
    rec, st, why = measure_one(m["sub_c"], m["video_c"])
    if rec is None:
        log(f"{m['title']}: could not measure; skipped"); return None
    tier = rep.tier_of(rec)
    log(f"{m['title']}: current {st} {why[:2]} | cues {rec['cues']} cov {rec['last_cue_end'] / rec['dur']:.0%}" + (f" -> Tier {tier}" if st == "FAIL" else ""))
    if st != "FAIL":
        log("  not a replacement target"); return None
    if plan_only:
        normal, whisper = candidates(m, set())
        log(f"  would try up to {len(normal)} candidates in {rep.NORMAL_BUDGET_S // 60} min, then whisperai ({len(whisper)} available)")
        for c in normal[:6]:
            log(f"    {c['provider']} score {c['orig_score']} hi={c['hearing_impaired']} {str(c['release_info'])[:55]}")
        return None
    outcome = run_episode(m, tier, rec)
    log(f"{m['title']}: RESULT {outcome}")
    return outcome


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", nargs="+"); g.add_argument("--run", nargs="+"); g.add_argument("--restore")
    a = ap.parse_args()
    os.makedirs(rep.ROOT, exist_ok=True)
    lk = open(rep.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another subtitle-replace run holds the lock")
    rep.recover()
    if a.restore:
        m = find_episode(a.restore)
        base = os.path.join(rep.ROOT, "backups")
        dirs = sorted(dp for dp, _, fs in os.walk(base) if "manifest.json" in fs and json.load(open(os.path.join(dp, "manifest.json")))["title"] == m["title"])
        if not dirs:
            sys.exit("no backup found")
        man = json.load(open(dirs[-1] + "/manifest.json"))
        rep.put_back(os.path.join(dirs[-1], os.path.basename(man["orig_path"])), man["orig_path"])
        ledger({"title": m["title"], "kind": "episode", "event": "restored", "from": dirs[-1]})
        print("restored", man["orig_path"]); return
    for text in (a.plan or a.run):
        process(find_episode(text), bool(a.plan))


if __name__ == "__main__":
    main()
