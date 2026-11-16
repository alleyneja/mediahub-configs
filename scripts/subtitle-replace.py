#!/usr/bin/env python3
"""Subtitle replace tool, T0 pilot (issue #73, docs/subtitle-replace-t0.md). MOVIES ONLY, run by hand, not cron.

  subtitle-replace.py --plan  TITLE...   measure the current English sub, show its tier and the candidates; changes nothing
  subtitle-replace.py --run   TITLE...   replace (tiered, see below), one title at a time
  subtitle-replace.py --restore TITLE    put the newest backup back

Tier A (useless: last cue before 50% of the runtime, or fewer than 20 cues): back up, remove, then try candidates.
Tier B (playable but off): back up, let Bazarr write a candidate over it, measure, and PUT THE BACKUP BACK unless the
candidate passes rule v1 (the current file is a FAIL, so a PASS is strictly better). Bazarr's download call writes
straight to the library path, so "download then swap" is "snapshot, download, verify, restore on failure".
Up to 3 normal candidates, then one whisperai candidate; none passing = "unresolved" (Tier B keeps the old file).

Never: touches Spanish, changes Bazarr settings, runs while Plex has a MOVIE stream (episodes and live TV do not block; unknown count does), uses PATCH search.
Reads the Bazarr API key from its config at run time; never prints it.
"""
import argparse, datetime, fcntl, importlib.util, json, os, re, shutil, sqlite3, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gate", os.path.join(HERE, "subtitle-gate.py"))
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)

ROOT = os.path.expanduser("~/subtitle-sweep")
LEDGER, LOCK = os.path.join(ROOT, "ledger.jsonl"), os.path.join(ROOT, "lock")
BAZARR_CFG = "/srv/docker/bazarr/config/config.yaml"
API = "http://127.0.0.1:6767/api"
NORMAL_BUDGET_S, SETTLE_S, SETTLE_MAX_S = 1200, 45, 1500   # time spent on regular candidates before whisperai


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def ledger(row):
    os.makedirs(ROOT, exist_ok=True)
    row["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LEDGER, "a") as fh:
        fh.write(json.dumps(row) + "\n")


def key():
    return re.search(r"^auth:.*?apikey:\s*(\S+)", open(BAZARR_CFG).read(), re.S | re.M).group(1).strip("'\"")


def api(method, path, params, timeout=1800):
    q = urllib.parse.urlencode(params)
    post = method == "POST"
    req = urllib.request.Request(f"{API}{path}" + ("" if post else "?" + q), method=method,
                                 data=q.encode() if post else None, headers={"X-API-KEY": key()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def ro_db():
    return sqlite3.connect(f"file:{gate.BAZARR_DB}?mode=ro", uri=True, timeout=30)


def find_movie(text):
    rows = ro_db().execute("select radarrId,title,path from table_movies where title like ?", (f"%{text}%",)).fetchall()
    if len(rows) != 1:
        sys.exit(f"'{text}' matches {len(rows)} movies: {[r[1] for r in rows]}")
    rid, title, path = rows[0]
    h = ro_db().execute("select id,provider,subs_id,language,subtitles_path from table_history_movie where radarrId=? "
                        "and action in (1,2,3) and language in ('en','en:hi') order by id desc limit 1", (rid,)).fetchone()
    if not h:
        sys.exit(f"{title}: no English download in Bazarr history")
    return {"rid": rid, "title": title, "video_c": path, "video": gate.host_path(path), "hist": h,
            "sub_c": h[4], "sub": gate.host_path(h[4]), "lang": h[3]}


def measure_one(sub_c, video_c):
    item = {"sub_c": sub_c, "video_c": video_c, "sub": gate.host_path(sub_c)}
    rec = gate.measure([item]).get(item["sub"])
    if not rec:
        return None, None, ["no measurement"]
    st, why = gate.judge(rec)
    return rec, st, why


def tier_of(rec):
    cues = rec.get("cues", 0)
    cov = rec["last_cue_end"] / rec["dur"] if rec.get("dur") else 0
    return "A" if (cov < 0.5 or cues < 20) else "B"


MOVIE_QUERY = gate.PLEX_QUERY.replace(
    'print(json.load(sys.stdin)[\\"MediaContainer\\"].get(\\"size\\",0))',
    'm=json.load(sys.stdin)[\\"MediaContainer\\"]; print(sum(1 for x in m.get(\\"Metadata\\",[]) if x.get(\\"type\\")==\\"movie\\" and x.get(\\"live\\")!=\\"1\\" and not str(x.get(\\"key\\",\\"\\")).startswith(\\"/livetv\\")), m.get(\\"size\\",0))')


def guard():
    """Stop only for a VOD MOVIE stream (this tool touches movie files only). Episodes and live TV do not block it
    (Plex reports Live TV as type "movie" with live=1 and a /livetv key, so those are excluded explicitly).
    An unreadable answer counts as busy. If r9 does not answer ping at all, nobody is watching."""
    import subprocess
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jay@192.168.0.22", MOVIE_QUERY],
                           capture_output=True, text=True, timeout=30)
        parts = r.stdout.split()
        if len(parts) == 2 and all(x.isdigit() for x in parts):
            if int(parts[0]) > 0:
                sys.exit(f"STOP: {parts[0]} movie stream(s) active ({parts[1]} stream(s) in all); nothing further done")
            return
    except (subprocess.TimeoutExpired, OSError):
        pass
    if subprocess.run(["ping", "-c1", "-W2", "192.168.0.22"], capture_output=True).returncode == 0:
        sys.exit("STOP: Plex stream count unknown; nothing further done")


def ledger_tried(title):
    """(provider, release, uploader) of every candidate already measured for this title in earlier runs."""
    out = set()
    if os.path.exists(LEDGER):
        for l in open(LEDGER):
            d = json.loads(l)
            if d.get("title") == title and d.get("event") == "candidate" and d.get("release") is not None:
                out.add((d["provider"], d["release"], d.get("uploader")))
    return out


def candidates(m, tried):
    st, body = api("GET", "/providers/movies", {"radarrid": m["rid"]}, 600)
    if st != 200:
        sys.exit(f"provider search failed: {st}")
    bl = {str(r[0]) for r in ro_db().execute("select subs_id from table_blacklist_movie")}
    cur_hi = m["lang"].endswith(":hi")
    en = [c for c in json.loads(body)["data"] if c["language"].split(":")[0] == "en" and c["subtitle"] not in bl
          and (c["provider"], c["subtitle"]) not in tried and c["subtitle"] != m["hist"][2]]
    past = ledger_tried(m["title"])
    en = [c for c in en if (c["provider"], str(c["release_info"]).lower(), str(c["uploader"])) not in past]
    en.sort(key=lambda c: (-c["orig_score"], c["hearing_impaired"] != cur_hi))
    seen, first, rest = set(), [], []                  # one try per distinct release name first, repeats of a release last
    for c in en:
        k = str(c["release_info"]).lower()
        (rest if k in seen else first).append(c)
        seen.add(k)
    en = first + rest
    normal = [c for c in en if c["provider"] != "whisperai"]
    whisper = [c for c in en if c["provider"] == "whisperai"]
    return normal, whisper


def settle(path):
    t0 = time.time()
    while time.time() - t0 < SETTLE_MAX_S:
        if os.path.isfile(path) and time.time() - os.stat(path).st_mtime > SETTLE_S and not os.path.exists(path[:-4] + ".synced.srt"):
            return True
        time.sleep(10)
    return False


def jobs_busy(m):
    """Bazarr queues downloads and syncs as jobs (a POST returns 204 at once). Count jobs for this movie or any pending manual download."""
    st, body = api("GET", "/system/jobs", {}, 60)
    if st != 200:
        return 1                                   # unknown counts as busy
    return sum(1 for j in json.loads(body)["data"]
               if j["status"] in ("pending", "running") and (m["title"] in j["job_name"] or "Manually downloading" in j["job_name"]))


def drain(m, max_s=2400):
    t0 = time.time()
    while time.time() - t0 < max_s:
        if jobs_busy(m) == 0:
            return True
        time.sleep(15)
    return False


def download(m, c):
    """POST one candidate, wait for Bazarr's job queue to empty, return (status, path-of-the-new-file or None, note).
    Bazarr answers 200/204 for an accepted job; the file lands later and the history row after that."""
    if not drain(m, 600):
        return 0, None, "Bazarr already busy with this title; not starting another job"
    t_post = time.time()
    before = ro_db().execute("select coalesce(max(id),0) from table_history_movie").fetchone()[0]
    st, body = api("POST", "/providers/movies", {"radarrid": m["rid"], "hi": str(c["hearing_impaired"]), "forced": "False",
                   "original_format": str(c["original_format"]), "provider": c["provider"], "subtitle": c["subtitle"]})
    if st not in (200, 204):
        return st, None, body[:160]
    time.sleep(5)
    if not drain(m):
        return st, None, "Bazarr jobs did not finish in time"
    time.sleep(5)
    row = ro_db().execute("select id,subtitles_path from table_history_movie where radarrId=? and id>? and action in (1,2,3) "
                          "and language in ('en','en:hi') order by id desc limit 1", (m["rid"], before)).fetchone()
    if row:
        return st, row[1], ""
    d = os.path.dirname(m["sub"])                  # no history row: take an English sub written since the POST
    new = [f for f in os.listdir(d) if re.search(r"\.en(\.hi)?\.srt$", f) and os.stat(os.path.join(d, f)).st_mtime >= t_post - 2]
    if new:
        return st, gate_c(os.path.join(d, new[0])), ""
    return st, None, "no new English subtitle appeared"


def gate_c(host):
    return gate.CONTAINER_PREFIX + host[len(gate.HOST_PREFIX):]


def backup(m):
    d = os.path.join(ROOT, "backups", f"{datetime.date.today():%Y-%m-%d}", re.sub(r"[^\w.()-]+", "_", m["title"]))
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(m["sub"]))
    if not os.path.exists(dst):
        shutil.copy2(m["sub"], dst)
    json.dump({"title": m["title"], "orig_path": m["sub"], "orig_path_c": m["sub_c"], "hist": list(m["hist"])}, open(d + "/manifest.json", "w"))
    return dst


def put_back(bak, dest):
    tmp = dest + ".restore.tmp"
    shutil.copy2(bak, tmp)
    os.replace(tmp, dest)


def blacklist(m, c_provider, c_subs_id, path_c):
    """Add a blacklist row; the API also deletes subtitles_path if it exists, so callers pass a path that is safe to lose."""
    st, body = api("POST", "/movies/blacklist", {"radarrid": m["rid"], "provider": c_provider, "subs_id": c_subs_id,
                                                 "language": "en", "subtitles_path": path_c})
    row = ro_db().execute("select count(*) from table_blacklist_movie where subs_id=?", (str(c_subs_id),)).fetchone()[0]
    return st, bool(row)


def run_title(m, tier, rec, apply_):
    guard()
    bak = backup(m)
    ledger({"title": m["title"], "event": "start", "tier": tier, "backup": bak, "current": rec.get("windows") and "measured"})
    if tier == "A":
        st, ok = blacklist(m, m["hist"][1], m["hist"][2], m["sub_c"])
        log(f"Tier A: blacklisted current ({st}, row={ok}); file gone={not os.path.exists(m['sub'])}")
        if os.path.exists(m["sub"]):
            st, _ = api("DELETE", "/movies/subtitles", {"radarrid": m["rid"], "language": "en", "forced": "False",
                        "hi": str(m["lang"].endswith(":hi")), "path": m["sub_c"]})
            log(f"  deleted via API ({st}); gone={not os.path.exists(m['sub'])}")
    tried, tries, outcome = set(), 0, "unresolved"
    normal, whisper = candidates(m, tried)
    log(f"{len(normal)} usable candidates + {len(whisper)} whisperai")
    queue = normal + whisper[:1]
    t_start = time.time()
    try:
        for c in queue:
            if c["provider"] != "whisperai" and time.time() - t_start > NORMAL_BUDGET_S:
                log("  time budget for regular candidates used up; skipping to whisperai"); continue
            guard()
            tries += 1
            tried.add((c["provider"], c["subtitle"]))
            log(f"try {tries}: {c['provider']} score {c['orig_score']} hi={c['hearing_impaired']} | {str(c['release_info'])[:60]}")
            st, newpath_c, note = download(m, c)
            if st not in (200, 204) or not newpath_c:
                log(f"  download failed ({st}) {note}"); ledger({"title": m["title"], "event": "download_failed", "cand": c["subtitle"], "status": st})
                if tier == "B":
                    put_back(bak, m["sub"])        # whatever Bazarr may have written, the snapshot goes back
                continue
            newpath = gate.host_path(newpath_c)
            if not settle(newpath):
                log("  file never settled; restoring" if tier == "B" else "  file never settled")
            guard()
            nrec, nst, why = measure_one(newpath_c, m["video_c"])
            log(f"  candidate -> {nst} {why[:2]}")
            ledger({"title": m["title"], "event": "candidate", "provider": c["provider"], "subs_id": c["subtitle"], "release": str(c["release_info"]).lower(),
                "uploader": str(c["uploader"]), "state": nst, "why": why, "path": newpath})
            if nst == "PASS":
                if newpath != m["sub"] and tier == "B" and os.path.exists(m["sub"]):
                    os.remove(m["sub"])           # the old, backed-up, worse file; the better one passed
                outcome = "replaced"; log(f"  ACCEPTED at {newpath}")
                break
            # rejected
            if tier == "B":
                if newpath == m["sub"]:
                    put_back(bak, m["sub"])
                elif os.path.exists(newpath):
                    os.remove(newpath)
                bst, brow = blacklist(m, c["provider"], c["subtitle"], newpath_c + ".rejected")   # path that does not exist: row only
            else:
                bst, brow = blacklist(m, c["provider"], c["subtitle"], newpath_c)                   # Tier A: deleting it is intended
                if os.path.exists(newpath):
                    api("DELETE", "/movies/subtitles", {"radarrid": m["rid"], "language": "en", "forced": "False",
                        "hi": str(c["hearing_impaired"]), "path": newpath_c})
            log(f"  rejected; blacklist {bst} row={brow}; restored/removed={'yes' if tier == 'B' else 'removed'}")
    except BaseException:                      # includes the Plex guard stopping the run mid-title
        if tier == "B" and outcome != "replaced":
            put_back(bak, m["sub"]); log("interrupted: Tier B snapshot restored")
        ledger({"title": m["title"], "event": "interrupted", "tier": tier, "outcome": outcome})
        raise
    ledger({"title": m["title"], "event": "end", "tier": tier, "outcome": outcome, "tries": tries})
    return outcome


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", nargs="+"); g.add_argument("--run", nargs="+"); g.add_argument("--restore")
    a = ap.parse_args()
    os.makedirs(ROOT, exist_ok=True)
    lk = open(LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another subtitle-replace run holds the lock")
    if a.restore:
        m = find_movie(a.restore)
        base = os.path.join(ROOT, "backups")
        dirs = sorted((os.path.join(dp) for dp, _, fs in os.walk(base) if "manifest.json" in fs
                       and json.load(open(os.path.join(dp, "manifest.json")))["title"] == m["title"]))
        if not dirs:
            sys.exit("no backup found")
        man = json.load(open(dirs[-1] + "/manifest.json"))
        put_back(os.path.join(dirs[-1], os.path.basename(man["orig_path"])), man["orig_path"])
        ledger({"title": m["title"], "event": "restored", "from": dirs[-1]})
        print("restored", man["orig_path"], "(Bazarr blacklist rows are left; they only stop the same sub returning)")
        return
    for text in (a.plan or a.run):
        m = find_movie(text)
        guard()
        rec, st, why = measure_one(m["sub_c"], m["video_c"])
        if rec is None:
            log(f"{m['title']}: could not measure; skipped"); continue
        tier = tier_of(rec)
        log(f"{m['title']}: current {st} {why[:2]} | cues {rec['cues']} cov {rec['last_cue_end'] / rec['dur']:.0%} -> Tier {tier}"
            if st == "FAIL" else f"{m['title']}: current is {st}; not a replacement target")
        if st != "FAIL":
            continue
        if a.plan:
            normal, whisper = candidates(m, set())
            log(f"  would try up to {len(normal)} candidates in {NORMAL_BUDGET_S // 60} min, then whisperai ({len(whisper)} available)")
            for c in normal[:5]:
                log(f"    {c['provider']} score {c['orig_score']} hi={c['hearing_impaired']} {str(c['release_info'])[:55]}")
        else:
            log(f"{m['title']}: RESULT {run_title(m, tier, rec, True)}")


if __name__ == "__main__":
    main()
