#!/usr/bin/env python3
"""Subtitle quality gate: REPORT-ONLY (issue #73, docs/subtitle-quality-gate.md section 8).

Looks at English subtitles Bazarr downloaded recently, measures each against the video's audio (7 x 480 s windows,
ffsubsync, run inside the bazarr container), applies rule v1 and records PASS / FAIL / UNVERIFIABLE in a ledger.
It CHANGES NOTHING: no file is edited, deleted, renamed or blacklisted, and no Bazarr setting is touched. Bazarr is read
only through a snapshot of its history database.

  subtitle-gate.py                  one run (cron: Tue/Thu/Sat 04:45, see docs/monitoring-inventory.md)
  subtitle-gate.py --dry-run        list what a run WOULD measure; measures and records nothing
  subtitle-gate.py --summary        counts, flagged files, review progress against the report-only exit
  subtitle-gate.py --review TEXT good|bad|unsure   record Jay's eyes-on verdict for a flagged file (path contains TEXT)
  subtitle-gate.py --backfill-days N   (first run only) also look back N days instead of starting from "now"

Safety (docs/subtitle-quality-gate.md section 1): never runs while Plex has a stream or when that is unknown; low CPU
priority; one file at a time; stops at --max-minutes; fails open (any error just ends the run, files untouched).
"""
import argparse, datetime, fcntl, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BAZARR_DB = "/srv/docker/bazarr/db/bazarr.db"
CONTAINER = "bazarr"
CONTAINER_PREFIX, HOST_PREFIX = "/data/", "/mnt/media/"
STATE_DIR = os.path.expanduser("~/logs/subtitle-gate")
STATE, LEDGER, REVIEWS, LOG = (os.path.join(STATE_DIR, n) for n in ("state.json", "ledger.jsonl", "reviews.jsonl", "gate.log"))
RULE_FILE = os.path.join(HERE, "subtitle-gate-benchmark", "chosen_rule_v1.json")
MEASURER = os.path.join(HERE, "subtitle-gate-benchmark", "measure_container.py")
ENV_FILE = os.path.join(HERE, "subtitle-gate.env")
PLEX_QUERY = ('T=$(sudo -n grep -o "PlexOnlineToken=\\"[^\\"]*\\"" "/srv/docker/plex/Library/Application Support/Plex Media Server/'
              'Preferences.xml" | cut -d\\" -f2); curl -s -m 10 -H "Accept: application/json" '
              '"http://127.0.0.1:32400/status/sessions?X-Plex-Token=$T" | python3 -c "import json,sys; '
              'print(json.load(sys.stdin)[\\"MediaContainer\\"].get(\\"size\\",0))"')
SETTLE_SECONDS = 600          # a download is measured only after the file has been untouched this long (post-download sync rewrites it)
BATCH = 5                     # files per measurement batch; Plex is re-checked between batches
MAX_QUEUE = 200               # a normal week is ~200 English downloads; more means a wrong checkpoint, so refuse
EXIT_MIN_GATED = 30           # report-only exit: at least this many measured downloads, every flag reviewed (parent record section 4)


def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


# ---------------------------------------------------------------- rule v1 (benchmarked in docs section 8)
def load_rule():
    with open(RULE_FILE) as fh:
        return json.load(fh)["params"]


def judge(rec, p=None):
    """Return (state, [reasons]) for one measurement record. PASS / FAIL (positive evidence) / UNVERIFIABLE."""
    p = p or load_rule()
    ws = [w for w in rec["windows"] if w]
    offs = [w["offset"] for w in ws]
    cutoff = p["usable_cutoff_s"]
    use = [o for o in offs if abs(o) <= cutoff]
    big = [o for o in offs if abs(o) > cutoff]
    cov = (rec["last_cue_end"] / rec["dur"]) if rec.get("dur") else 0
    why = []
    if len(ws) < p["min_windows_with_10_cues"]:
        why.append(f"only {len(ws)} of 7 windows have enough subtitle cues (missing dialogue or truncated)")
    if p["min_coverage_last_cue_over_duration"] and cov < p["min_coverage_last_cue_over_duration"]:
        why.append(f"last cue ends at {cov * 100:.0f}% of the runtime")
    if use and max(abs(o) for o in use) > p["max_abs_offset_s"]:
        worst = max(use, key=abs)
        why.append(f"a usable window reads {worst:+.1f} s")
    if use and max(use) - min(use) > p["max_spread_s"]:
        why.append(f"usable windows disagree by {max(use) - min(use):.1f} s (drift or a different cut)")
    for b in big:
        if sum(1 for o in big if abs(o - b) <= 2.0) >= 3:
            why.append(f"3 or more windows agree on a large offset (~{b:+.0f} s)")
            break
    if why:
        return "FAIL", why
    if len(use) < p["min_usable_windows"]:
        return "UNVERIFIABLE", [f"only {len(use)} usable windows (low-speech film?); nothing contradicts the subtitle"]
    return "PASS", []


# ---------------------------------------------------------------- guards
def plex_busy():
    """(busy, reason). Unknown counts as busy. If r9 does not answer ping at all, nobody can be watching."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jay@192.168.0.22", PLEX_QUERY],
                           capture_output=True, text=True, timeout=30)
        n = r.stdout.strip()
        if n.isdigit():
            return (int(n) > 0, f"Plex streams: {n}" if int(n) else "")
    except (subprocess.TimeoutExpired, OSError):
        pass
    if subprocess.run(["ping", "-c1", "-W2", "192.168.0.22"], capture_output=True).returncode != 0:
        return False, ""
    return True, "Plex stream count unknown"


# ---------------------------------------------------------------- candidates from Bazarr's history (read-only snapshot)
def snapshot():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    src = sqlite3.connect(f"file:{BAZARR_DB}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    return dst, tmp


def host_path(p):
    return HOST_PREFIX + p[len(CONTAINER_PREFIX):] if p and p.startswith(CONTAINER_PREFIX) else p


def candidates(conn, state, now=None):
    """New English (regular + HI) downloads/upgrades since the checkpoints, newest row per subtitle path, settled on disk."""
    now = now or time.time()
    out, skipped = [], []
    for table, key in (("table_history", "episodes"), ("table_history_movie", "movies")):
        since = state["since"].get(key, 0)
        rows = conn.execute(
            f"select id,action,timestamp,video_path,language,provider,score,subtitles_path from {table} "
            f"where id>? and action in (1,2,3) and language in ('en','en:hi') order by id", (since,)).fetchall()
        latest = {}
        for r in rows:
            latest[r[7]] = r
        for r in latest.values():
            sub, vid = host_path(r[7]), host_path(r[3])
            item = {"table": key, "history_id": r[0], "action": r[1], "language": r[4], "provider": r[5], "score": r[6],
                    "sub_c": r[7], "video_c": r[3], "sub": sub, "video": vid}
            if not (sub and os.path.isfile(sub) and vid and os.path.isfile(vid)):
                skipped.append((item, "missing on disk")); continue
            st = os.stat(sub)
            if now - st.st_mtime < SETTLE_SECONDS or os.path.exists(sub[:-4] + ".synced.srt"):
                skipped.append((item, "still settling")); continue
            item["fp"] = f"{st.st_size}:{st.st_mtime_ns}"
            out.append(item)
        state.setdefault("maxseen", {})[key] = max([r[0] for r in rows], default=since)
    return out, skipped


def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER) if l.strip()]


# ---------------------------------------------------------------- measurement (inside the bazarr container)
def measure(items):
    work = tempfile.mkdtemp(prefix="gate-")
    jobs = [{"title": f"j{i}", "variant": "live", "srt": it["sub_c"], "video": it["video_c"]} for i, it in enumerate(items)]
    json.dump(jobs, open(f"{work}/jobs.json", "w"))
    try:
        subprocess.run(["docker", "cp", MEASURER, f"{CONTAINER}:/tmp/measure_container.py"], check=True, capture_output=True)
        subprocess.run(["docker", "cp", f"{work}/jobs.json", f"{CONTAINER}:/tmp/gate_jobs.json"], check=True, capture_output=True)
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", "/tmp/gate_out.jsonl"], check=True, capture_output=True)
        subprocess.run(["docker", "exec", "-e", "PYTHONPATH=/app/bazarr/bin/libs", CONTAINER, "nice", "-n", "19", "python3",
                        "/tmp/measure_container.py", "/tmp/gate_jobs.json", "/tmp/gate_out.jsonl", "1"],
                       check=True, capture_output=True, text=True, timeout=3600)
        subprocess.run(["docker", "cp", f"{CONTAINER}:/tmp/gate_out.jsonl", f"{work}/out.jsonl"], check=True, capture_output=True)
        res = {}
        for l in open(f"{work}/out.jsonl"):
            r = json.loads(l)
            res[r["title"]] = r
        return {items[int(k[1:])]["sub"]: v for k, v in res.items()}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- reporting
def discord(msg):
    if not os.path.exists(ENV_FILE):
        return
    url = next((l.split("=", 1)[1].strip() for l in open(ENV_FILE) if l.startswith("DISCORD_WEBHOOK_URL=")), "")
    if url:
        try:
            req = urllib.request.Request(url, data=json.dumps({"content": msg[:1900]}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "mediahub-subtitle-gate"})
            urllib.request.urlopen(req, timeout=10)
        except OSError as e:
            log(f"discord post failed: {e}")


def nice_name(path):
    return os.path.basename(path).rsplit(".", 2)[0]


def summary():
    rows = read_ledger()
    reviews = {}
    for l in open(REVIEWS) if os.path.exists(REVIEWS) else []:
        r = json.loads(l); reviews[r["path"]] = r["verdict"]
    by = {"PASS": 0, "FAIL": 0, "UNVERIFIABLE": 0}
    for r in rows:
        by[r["state"]] += 1
    flagged = [r for r in rows if r["state"] == "FAIL"]
    reviewed = [r for r in flagged if r["path"] in reviews]
    bad = sum(1 for r in reviewed if reviews[r["path"]] == "bad")
    print(f"gated downloads: {len(rows)}   PASS {by['PASS']}   FAIL {by['FAIL']}   UNVERIFIABLE {by['UNVERIFIABLE']}")
    print(f"flagged (FAIL) reviewed by eye: {len(reviewed)} of {len(flagged)}; confirmed bad {bad}"
          + (f"  -> live precision {100 * bad // len(reviewed)}%" if reviewed else ""))
    ready = len(rows) >= EXIT_MIN_GATED and len(reviewed) == len(flagged)
    print(f"report-only exit (>= {EXIT_MIN_GATED} gated, every flag reviewed): {'MET' if ready else 'not yet'}")
    for r in flagged:
        print(f"  [{reviews.get(r['path'], 'unreviewed'):10s}] {nice_name(r['path'])} ({r['provider']}, score {r['score']}): {'; '.join(r['reasons'])}")
    for r in rows:
        if r["state"] == "UNVERIFIABLE":
            print(f"  [unverifiable] {nice_name(r['path'])}: {r['reasons'][0]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--review", nargs=2, metavar=("TEXT", "VERDICT"))
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--max-minutes", type=int, default=120)
    ap.add_argument("--ignore-plex", action="store_true", help="testing only, when you know nobody is watching")
    a = ap.parse_args()
    os.makedirs(STATE_DIR, exist_ok=True)
    if a.summary:
        return summary()
    if a.review:
        text, verdict = a.review
        if verdict not in ("good", "bad", "unsure"):
            sys.exit("verdict must be good, bad or unsure")
        hits = sorted({r["path"] for r in read_ledger() if r["state"] == "FAIL" and text.lower() in r["path"].lower()})
        if len(hits) != 1:
            sys.exit(f"{len(hits)} flagged files match {text!r}: {hits}")
        open(REVIEWS, "a").write(json.dumps({"path": hits[0], "verdict": verdict, "at": time.time()}) + "\n")
        print(f"recorded {verdict}: {nice_name(hits[0])}")
        return
    lock = open(os.path.join(STATE_DIR, "lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another run is in progress; exiting")
    try:
        conn, tmp = snapshot()
        state = json.load(open(STATE)) if os.path.exists(STATE) else None
        if state is None:
            if a.backfill_days:
                cutoff = (datetime.datetime.now() - datetime.timedelta(days=a.backfill_days)).strftime("%Y-%m-%d %H:%M:%S")
                # first id inside the window minus one; an EMPTY window must mean "start from now", never "from id 0"
                since = {}
                for k, t in (("episodes", "table_history"), ("movies", "table_history_movie")):
                    first = conn.execute(f"select min(id) from {t} where timestamp>=?", (cutoff,)).fetchone()[0]
                    since[k] = (first - 1) if first else conn.execute(f"select coalesce(max(id),0) from {t}").fetchone()[0]
            else:
                since = {k: conn.execute(f"select coalesce(max(id),0) from {t}").fetchone()[0]
                         for k, t in (("episodes", "table_history"), ("movies", "table_history_movie"))}
            state = {"since": since}
            log(f"first run: starting after history ids {since}")
        todo, skipped = candidates(conn, state)
        seen = {(r["path"], r["fp"]) for r in read_ledger()}
        todo = [t for t in todo if (t["sub"], t["fp"]) not in seen]
        if len(todo) > MAX_QUEUE and not a.dry_run:
            return log(f"refusing to queue {len(todo)} files (> {MAX_QUEUE}); checkpoint looks wrong, nothing measured or saved")
        log(f"{len(todo)} to measure, {len(skipped)} not ready ({', '.join(sorted({s[1] for s in skipped})) or 'none'})")
        if a.dry_run:
            for t in todo:
                print("  would measure:", nice_name(t["sub"]), f"[{t['provider']} score {t['score']}]")
            return
        rule, started, results = load_rule(), time.time(), []
        for i in range(0, len(todo), BATCH):
            if time.time() - started > a.max_minutes * 60:
                log("time cap reached; remaining files wait for the next run"); break
            if not a.ignore_plex:
                busy, why = plex_busy()
                if busy:
                    log(f"stopping: {why}; remaining files wait for the next run"); break
            batch = todo[i:i + BATCH]
            measured = measure(batch)
            for it in batch:
                rec = measured.get(it["sub"])
                if not rec:
                    log(f"no measurement for {nice_name(it['sub'])}"); continue
                st, why = judge(rec, rule)
                row = {"ts": time.time(), "path": it["sub"], "fp": it["fp"], "provider": it["provider"], "score": it["score"],
                       "action": it["action"], "language": it["language"], "table": it["table"], "history_id": it["history_id"],
                       "state": st, "reasons": why, "dur": rec["dur"], "cues": rec["cues"], "last_cue_end": rec["last_cue_end"],
                       "windows": rec["windows"]}
                open(LEDGER, "a").write(json.dumps(row) + "\n")
                results.append(row)
        # advance the checkpoints only past ids whose files were fully handled this run, never past a still-settling file
        waiting = [s[0] for s in skipped if s[1] == "still settling"] + [t for t in todo if t["sub"] not in {r["path"] for r in results}]
        for key in ("episodes", "movies"):
            floor = min([w["history_id"] for w in waiting if w["table"] == key], default=None)
            state["since"][key] = (floor - 1) if floor else state["maxseen"][key]
        json.dump({"since": state["since"]}, open(STATE, "w"))
        counts = {s: sum(1 for r in results if r["state"] == s) for s in ("PASS", "FAIL", "UNVERIFIABLE")}
        log(f"measured {len(results)}: {counts}")
        fails = [r for r in results if r["state"] == "FAIL"]
        if fails:
            lines = [f"**Subtitle gate (report-only):** {len(fails)} of {len(results)} new English subs flagged; nothing was changed."]
            lines += [f"- {nice_name(r['path'])} ({r['provider']}, score {r['score']}): {'; '.join(r['reasons'])}" for r in fails[:10]]
            discord("\n".join(lines))
    except Exception as e:   # fail open: a broken gate must never affect downloads or files
        log(f"run failed, files untouched: {type(e).__name__}: {e}")
    finally:
        try:
            os.unlink(tmp)
        except (NameError, OSError):
            pass


if __name__ == "__main__":
    main()
