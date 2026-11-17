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


def _movie(rid, title, path):
    h = ro_db().execute("select id,provider,subs_id,language,subtitles_path from table_history_movie where radarrId=? "
                        "and action in (1,2,3) and language in ('en','en:hi') order by id desc limit 1", (rid,)).fetchone()
    if not h:
        sys.exit(f"{title}: no English download in Bazarr history")
    return {"rid": rid, "title": title, "video_c": path, "video": gate.host_path(path), "hist": h,
            "sub_c": h[4], "sub": gate.host_path(h[4]), "lang": h[3]}


def find_movie(text):
    rows = ro_db().execute("select radarrId,title,path from table_movies where title like ?", (f"%{text}%",)).fetchall()
    if len(rows) != 1:
        sys.exit(f"'{text}' matches {len(rows)} movies: {[r[1] for r in rows]}")
    return _movie(*rows[0])


def movie_by_history_id(hid):
    """The movie a gate row belongs to (gate rows carry the Bazarr movie-history id), or None. Never matches by name."""
    row = ro_db().execute("select m.radarrId, m.title, m.path from table_history_movie h join table_movies m "
                          "on m.radarrId = h.radarrId where h.id=?", (hid,)).fetchone()
    return _movie(*row) if row else None


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


# Movie guard: counts VOD movie sessions, paused ones too (a paused movie still has its file open), and returns "movies all".
MOVIE_QUERY = gate.PLEX_QUERY_TMPL % ('print(sum(1 for x in m.get(\\"Metadata\\",[]) if x.get(\\"type\\")==\\"movie\\" and x.get(\\"live\\")!=\\"1\\" and not str(x.get(\\"key\\",\\"\\")).startswith(\\"/livetv\\")), m.get(\\"size\\",0))')


ALLOW_MOVIE_STREAMS = False     # one-off, by Jay's explicit say-so (--allow-movie-streams); an unknown stream count still stops the run


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
            if int(parts[0]) > 0 and not ALLOW_MOVIE_STREAMS:
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


# ---------------------------------------------------------------- subsource, queried directly (docs/subtitle-subsource-direct.md)
SS_BASE, SS_CAP_ZIP, SS_CAP_SRT, SS_PAUSE_S = "https://api.subsource.net/api/v1/", 6_000_000, 2_000_000, 2.0
SS_CALLS = 0


class RateStop(Exception):
    pass


def ss_key():
    return re.search(r"^subsource:\s*\n\s+apikey:\s*(\S+)", open(BAZARR_CFG).read(), re.M).group(1).strip("'\"")


def ss_get(path, params):
    """GET with the api key in the query string (as Bazarr's provider does). URLs are never logged or printed."""
    global SS_CALLS
    time.sleep(SS_PAUSE_S)
    SS_CALLS += 1
    req = urllib.request.Request(SS_BASE + path + "?" + urllib.parse.urlencode({**params, "api_key": ss_key()}),
                                 headers={"User-Agent": "subtitle-replace/1"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read(SS_CAP_ZIP + 1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateStop("subsource said slow down (429)")
        return e.code, b""
    except Exception as e:                      # type name only: the message could carry the URL
        return 0, type(e).__name__.encode()


def ss_candidates(imdb):
    st, body = ss_get("movies/search", {"searchType": "imdb", "imdb": imdb})
    d = json.loads(body).get("data") if st == 200 else None
    mid = (d[0].get("movieId") or d[0].get("id")) if d else None
    if not mid:
        return []
    st, body = ss_get("subtitles", {"language": "english", "limit": 100, "movieId": mid})
    items = json.loads(body).get("data", []) if st == 200 else []
    out, seen = [], set()
    for it in items:
        if it.get("foreignParts") or "forced" in str(it.get("commentary", "")).lower() or it["subtitleId"] in seen:
            continue
        seen.add(it["subtitleId"])
        up = next((c.get("displayname", "") for c in it.get("contributors", []) if c.get("id") == it.get("uploaderId")), "")
        out.append({"id": it["subtitleId"], "release": (" ".join(it["releaseInfo"]) if isinstance(it.get("releaseInfo"), list) else str(it.get("releaseInfo", ""))).lower(), "uploader": str(up), "hi": bool(it.get("hearingImpaired"))})
    return out


def ss_fetch(sid):
    """Download the zip to memory, return up to 3 .srt members as text. Member names are never used as paths."""
    import io, zipfile
    st, body = ss_get(f"subtitles/{sid}/download", {})
    if st != 200 or len(body) > SS_CAP_ZIP or not zipfile.is_zipfile(io.BytesIO(body)):
        return []
    zf, out = zipfile.ZipFile(io.BytesIO(body)), []
    for info in sorted(zf.infolist(), key=lambda i: -i.file_size):
        if info.filename.lower().endswith(".srt") and 0 < info.file_size <= SS_CAP_SRT and len(out) < 3:
            raw = zf.open(info).read(SS_CAP_SRT + 1)
            if len(raw) <= SS_CAP_SRT:
                for enc in ("utf-8-sig", "cp1252"):
                    try:
                        out.append(raw.decode(enc)); break
                    except UnicodeDecodeError:
                        continue
    return out


def ss_measure(text, video_c):
    """Measure a subtitle that is NOT in the library: copy it into the bazarr container's /tmp, measure, remove."""
    import subprocess, tempfile
    name = f"ssc_{os.getpid()}_{int(time.time() * 1000) % 10**9}.srt"
    with tempfile.TemporaryDirectory() as td:
        host = os.path.join(td, name)
        open(host, "w", encoding="utf-8").write(text)
        subprocess.run(["docker", "cp", host, f"{gate.CONTAINER}:/tmp/{name}"], check=True, capture_output=True)
    try:
        return measure_one(f"/tmp/{name}", video_c)
    finally:
        subprocess.run(["docker", "exec", gate.CONTAINER, "rm", "-f", f"/tmp/{name}"], capture_output=True)


SHIFT_MAX_SPREAD_S = 0.5     # usable windows must agree within this, else it is drift or another cut: never shifted
SHIFT_MIN_WINDOWS = 3
SHIFT_MAX_ABS_S = 10.0
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})(\s*-->\s*)(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def shift_plan(rec):
    """Median offset to correct, or None unless the usable windows agree (constant shift only, never drift)."""
    use = sorted(w["offset"] for w in rec.get("windows", []) if w and abs(w["offset"]) <= SHIFT_MAX_ABS_S)
    if len(use) < SHIFT_MIN_WINDOWS or use[-1] - use[0] > SHIFT_MAX_SPREAD_S:
        return None
    return use[len(use) // 2]


def shift_srt(text, delta):
    """Move every cue by delta seconds (clamped at 0). Text and numbering are untouched."""
    def t(h, m, sec, ms):
        return max(0.0, int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000 + delta)

    def fmt(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    def sub(mo):
        g = mo.groups()
        return f"{fmt(t(*g[0:4]))}{g[4]}{fmt(t(*g[5:9]))}"
    return _TS.sub(sub, text)


def shifted_pass(text, rec, video_c):
    """If rec is a clean constant shift, try correcting it in memory/temp only. Tries shifting by the measured offset itself (verified Nov 16), then the
    opposite (the measurer's sign convention is not assumed). Returns (shifted_text, delta, state, why, rec2) or None."""
    off = shift_plan(rec)
    if off is None:
        return None
    last = None
    for delta in (off, -off):
        txt = shift_srt(text, delta)
        rec2, st, why = ss_measure(txt, video_c)
        last = (txt, delta, st, why, rec2)
        if st == "PASS":
            return last
    return last


def imdb_of(m):
    return ro_db().execute("select imdbId from table_movies where radarrId=?", (m["rid"],)).fetchone()[0]


def phase0(titles):
    """Look only: fetch and measure every subsource English candidate for each title. Swaps nothing."""
    for t in titles:
        m = find_movie(t)
        log(f"== {m['title']} (imdb {imdb_of(m)}), current file left untouched")
        try:
            cands = ss_candidates(imdb_of(m))
            log(f"   {len(cands)} subsource English candidates (after dropping forced)")
            for c in cands:
                guard()
                texts = ss_fetch(c["id"])
                if not texts:
                    log(f"   {c['release'][:46]:46s} no usable .srt in the download"); continue
                for k, text in enumerate(texts):
                    rec, st, why = ss_measure(text, m["video_c"])
                    if rec is None:
                        log(f"   {c['release'][:46]:46s} could not measure"); continue
                    use = [w["offset"] for w in rec["windows"] if w and abs(w["offset"]) <= 10]
                    rng = f"{min(use):+.1f}..{max(use):+.1f}s" if use else "n/a"
                    log(f"   {c['release'][:46]:46s} hi={int(c['hi'])} {st:12s} offsets {rng:14s} cues {rec['cues']} cov {rec['last_cue_end'] / rec['dur']:.0%}")
                    ledger({"title": m["title"], "event": "phase0", "provider": "subsource", "release": c["release"], "uploader": c["uploader"],
                            "state": st, "why": why, "usable_offsets": use, "cues": rec["cues"]})
        except RateStop as e:
            log(f"   STOPPED: {e}")
    log(f"subsource API calls this run: {SS_CALLS}")


def phase1(titles):
    """Look only: for clean constant-shift subsource candidates, shift in memory, re-measure, report. Swaps nothing."""
    for t in titles:
        m = find_movie(t)
        log(f"== {m['title']} shift test, current file left untouched")
        try:
            for c in ss_candidates(imdb_of(m)):
                guard()
                for text in ss_fetch(c["id"]):
                    rec, st, why = ss_measure(text, m["video_c"])
                    if rec is None or st == "PASS":
                        continue
                    off = shift_plan(rec)
                    if off is None:
                        continue
                    r = shifted_pass(text, rec, m["video_c"])
                    txt, delta, st2, why2, rec2 = r
                    log(f"   {c['release'][:46]:46s} offset {off:+.2f}s, shifted {delta:+.2f}s -> {st2} {why2[:2]}")
                    ledger({"title": m["title"], "event": "phase1", "provider": "subsource", "release": c["release"],
                            "uploader": c["uploader"], "offset": off, "delta": delta, "state": st2, "why": why2})
        except RateStop as e:
            log(f"   STOPPED: {e}")
    log(f"subsource API calls this run: {SS_CALLS}")


def try_direct(m, bak, tier):
    """Real run: measure subsource candidates from temp files; on the first PASS put it in the library atomically."""
    past = {(prov, rel, up) for (prov, rel, up) in ledger_tried(m["title"])}
    try:
        for c in ss_candidates(imdb_of(m)):
            if ("subsource", c["release"], c["uploader"]) in past:
                continue
            guard()
            for text in ss_fetch(c["id"]):
                rec, st, why = ss_measure(text, m["video_c"])
                log(f"  subsource direct: {c['release'][:50]} -> {st} {why[:1]}")
                ledger({"title": m["title"], "event": "candidate", "provider": "subsource", "subs_id": c["id"], "release": c["release"],
                        "uploader": c["uploader"], "state": st, "why": why, "path": "direct"})
                if st != "PASS" and rec is not None and shift_plan(rec) is not None:
                    r = shifted_pass(text, rec, m["video_c"])
                    if r:
                        text, delta, st, why, rec = r
                        log(f"  subsource direct (shifted {delta:+.2f}s): {c['release'][:40]} -> {st} {why[:1]}")
                        ledger({"title": m["title"], "event": "candidate", "provider": "subsource", "subs_id": c["id"], "release": c["release"],
                                "uploader": c["uploader"], "state": st, "why": why, "path": "direct-shifted", "delta": delta})
                if st == "PASS":
                    tmp = m["sub"] + ".new.tmp"
                    open(tmp, "w", encoding="utf-8").write(text)
                    shutil.copymode(bak, tmp)
                    os.replace(tmp, m["sub"])
                    log(f"  ACCEPTED (subsource direct) at {m['sub']}")
                    return True
    except RateStop as e:
        log(f"  subsource direct stopped: {e}")
    return False


def run_title(m, tier, rec, apply_, direct=False):
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
    if direct and try_direct(m, bak, tier):
        ledger({"title": m["title"], "event": "end", "tier": tier, "outcome": "replaced", "via": "subsource-direct"})
        return "replaced"
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


def dangling_titles(rows):
    """Titles whose newest 'start' has no later end/interrupted/restored/recovered row, mapped to (start row, a candidate PASSed)."""
    open_ = {}
    for e in rows:
        t, ev = e.get("title"), e.get("event")
        if ev == "start":
            open_[t] = [e, False]
        elif t in open_ and ev == "candidate" and e.get("state") == "PASS":
            open_[t][1] = True
        elif t in open_ and ev in ("end", "interrupted", "restored", "recovered"):
            open_.pop(t)
    return {t: (v[0], v[1]) for t, v in open_.items()}


def recover():
    """Run once the lock is held, so nothing is in flight. A run killed outright (reboot, power cut, kill -9) never reaches
    its 'interrupted' handler. If the title started but never finished and no candidate PASSed, the library file may be an
    unvetted candidate or missing: put the backup back. If a candidate PASSed, the replacement stands; just close the row."""
    try:
        rows = [json.loads(l) for l in open(LEDGER)]
    except OSError:
        return
    for t, (start, passed) in dangling_titles(rows).items():
        base = os.path.join(ROOT, "backups")
        dirs = sorted((dp for dp, _, fs in os.walk(base) if "manifest.json" in fs
                       and json.load(open(os.path.join(dp, "manifest.json"))).get("title") == t), key=os.path.getmtime)
        if passed or not dirs:
            log(f"recover: {t}: {'a candidate had passed; leaving it' if passed else 'no backup found; needs a look'}")
            ledger({"title": t, "event": "recovered", "action": "left" if passed else "no-backup"}); continue
        man = json.load(open(dirs[-1] + "/manifest.json"))
        bak = os.path.join(dirs[-1], os.path.basename(man["orig_path"]))
        if not os.path.exists(man["orig_path"]) or os.path.getsize(man["orig_path"]) != os.path.getsize(bak):
            put_back(bak, man["orig_path"]); action = "restored"
        else:
            action = "unchanged"
        log(f"recover: {t}: run died before finishing; {action}")
        ledger({"title": t, "event": "recovered", "action": action, "from": dirs[-1]})


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", nargs="+"); g.add_argument("--run", nargs="+"); g.add_argument("--restore"); g.add_argument("--phase0", nargs="+"); g.add_argument("--phase1", nargs="+")
    ap.add_argument("--direct-subsource", action="store_true")
    ap.add_argument("--allow-movie-streams", action="store_true", help="one-off: do not stop for a movie stream (Jay said so for this run); unknown count still stops")
    a = ap.parse_args()
    global ALLOW_MOVIE_STREAMS
    ALLOW_MOVIE_STREAMS = a.allow_movie_streams
    if ALLOW_MOVIE_STREAMS:
        log("NOTE: movie streams will not stop this run (--allow-movie-streams, Jay's call)")
    os.makedirs(ROOT, exist_ok=True)
    lk = open(LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another subtitle-replace run holds the lock")
    recover()
    if a.phase0:
        phase0(a.phase0); return
    if a.phase1:
        phase1(a.phase1); return
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
        process(find_movie(text), bool(a.plan), a.direct_subsource)


def process(m, plan_only, direct):
    """Measure the current sub, decide the tier, and (unless plan_only) replace. Returns the outcome string or None."""
    guard()
    rec, st, why = measure_one(m["sub_c"], m["video_c"])
    if rec is None:
        log(f"{m['title']}: could not measure; skipped"); return None
    tier = tier_of(rec)
    log(f"{m['title']}: current {st} {why[:2]} | cues {rec['cues']} cov {rec['last_cue_end'] / rec['dur']:.0%} -> Tier {tier}"
        if st == "FAIL" else f"{m['title']}: current is {st}; not a replacement target")
    if st != "FAIL":
        return None
    if plan_only:
        normal, whisper = candidates(m, set())
        log(f"  would try up to {len(normal)} candidates in {NORMAL_BUDGET_S // 60} min, then whisperai ({len(whisper)} available)")
        for c in normal[:5]:
            log(f"    {c['provider']} score {c['orig_score']} hi={c['hearing_impaired']} {str(c['release_info'])[:55]}")
        return None
    outcome = run_title(m, tier, rec, True, direct)
    log(f"{m['title']}: RESULT {outcome}")
    return outcome


if __name__ == "__main__":
    main()
