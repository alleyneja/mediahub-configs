#!/usr/bin/env python3
"""Subtitle enforce wrapper (issue #73, docs/subtitle-enforce-mode.md). Movies always; TV episodes only after `--enable-tv`.

One tick (cron every 15 minutes, once approved for go-live):
  1. stop if ~/subtitle-sweep/PAUSE exists, or if another replace run holds the lock
  2. run the gate (measure new downloads; report-only, silent on Discord)
  3. for each NEW movie FAIL: replace it with subtitle-replace.py's process() (cap per tick)
  4. post to Discord for every replacement, every unresolved title, every "needs review" title

Never replaced, only reported as "needs review": coverage-only FAILs, a first audio stream that is not English or is a
commentary track. UNVERIFIABLE is ignored. Spanish files are never touched.
TV (off until Jay runs --enable-tv; docs/subtitle-tv-mode.md): after the movie step, new English episode downloads are measured with
rule tv-v1.1 and FAILs are replaced through subtitle-replace-tv.py. Caps per tick: 6 measured, 2 replaced, 12 replaced per day, 25 minutes.
A show that is being watched stays queued. A coverage-only FAIL or a file with no English audio is reported, not replaced.

  subtitle-enforce.py               one tick
  subtitle-enforce.py --dry-run     show what a tick WOULD do; runs no gate, replaces nothing, posts nothing
  subtitle-enforce.py --status      counters, pause state, spot-checks waiting
  subtitle-enforce.py --spot TITLE good|bad    record Jay's eyes-on check of a replacement (bad = writes PAUSE)
  subtitle-enforce.py --resume      remove PAUSE (Jay's decision only)
  subtitle-enforce.py --enable-tv / --disable-tv   switch TV on (from the newest download onward) or off
  subtitle-enforce.py --tv-dry-run HOURS           measure TV downloads of the last HOURS and say what a tick would do; changes nothing
"""
import argparse, datetime, fcntl, importlib.util, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


r = _load("replace", "subtitle-replace.py")
gate = r.gate
ROOT = r.ROOT
STATE_FILE, PAUSE = os.path.join(ROOT, "enforce-state.json"), os.path.join(ROOT, "PAUSE")
CAP = 5               # replacements per tick
SPOT_EVERY = 5        # one in five replacements is marked for Jay to eyeball (the first one included)
MAX_ATTEMPTS = 2      # a title that raises errors twice is dropped and reported
KEEP_HANDLED = 3000
GATE_MINUTES = 20
TV_CAP_MEASURE, TV_CAP_REPLACE, TV_DAILY_REPLACE, TV_BUDGET_S = 6, 2, 12, 25 * 60
# Backlog lane (old library content), off until --enable-backlog. Per tick, after new downloads: 5 min of TV, 5 min of movies, at most
# 2 replacements per pass, 40 replacements per day (TV and movies together), one Discord digest a day instead of a post per replacement.
BL_TV_SECONDS, BL_MOVIE_MINUTES, BL_PASS_REPLACE, BL_DAILY_REPLACE = 5 * 60, 5, 2, 40
BL_MAX_TICK_ELAPSED, BL_DIGEST_HOUR, BL_SPOT_N = 8 * 60, 8, 5


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


# ---------------------------------------------------------------- pure decisions (unit-tested)
def classify(row):
    """What the wrapper does with one gate row."""
    if row.get("table") != "movies":
        return "ignore"                       # TV stays report-only
    if row["state"] != "FAIL":
        return "ignore"                       # PASS, and UNVERIFIABLE is never replaced
    if row["reasons"] and all(x.startswith("last cue ends") for x in row["reasons"]):
        return "review-coverage"              # long credits look the same; needs the tail probe, never auto-replaced
    return "replace"


def pick_audio_index(streams):
    """Same rule as pick_audio() in subtitle-gate-benchmark/measure_container.py: first English, non-commentary track, else None."""
    for i, x in enumerate(streams):
        t = {k.lower(): str(v) for k, v in (x.get("tags") or {}).items()}
        if t.get("language", "").lower() in ("eng", "en") and "comment" not in t.get("title", "").lower():
            return i
    return None


def audio_ok(probe_json):
    """The film needs an English, non-commentary audio track for the measurer to use. Returns (ok, note).
    An untagged single track is accepted (older rips often carry no language tag)."""
    try:
        streams = json.loads(probe_json).get("streams", [])
    except ValueError:
        return False, "could not read the audio streams"
    if not streams:
        return False, "no audio stream found"
    if pick_audio_index(streams) is not None:
        return True, ""
    if len(streams) == 1 and not (streams[0].get("tags") or {}).get("language") and "comment" not in str((streams[0].get("tags") or {}).get("title", "")).lower():
        return True, ""
    return False, "no English, non-commentary audio track to measure against"


def plan_work(rows, state, since_ts):
    """Rows the gate produced since go-live that have not been handled yet, as (action, row), in order."""
    handled = set(state.get("handled", []))
    out = []
    for row in rows:
        if row["ts"] < since_ts or row_key(row) in handled:
            continue
        out.append((classify(row), row))
    return out


def current_fp(path):
    """Same fingerprint the gate stores (size:mtime_ns); a different one means the file changed since it was measured."""
    try:
        st = os.stat(path)
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def row_key(row):
    return f"{row['path']}|{row['fp']}"


def classify_tv(state, reasons):
    """What the wrapper does with one measured episode subtitle (rule tv-v1.1)."""
    if state != "FAIL":
        return "ignore"                       # PASS, and UNVERIFIABLE is never replaced
    if reasons and all(x.startswith("last cue ends") for x in reasons):
        return "review-coverage"              # a long ending or previews look the same; never auto-replaced
    return "replace"


def plan_tv_work(rows, handled):
    """rows: [{"id","eid","path","fp"}] in history order. Newest row per subtitle path, minus what is already handled, oldest first."""
    latest = {}
    for x in rows:
        latest[x["path"]] = x
    h = set(handled)
    return sorted((x for x in latest.values() if f"{x['path']}|{x['fp']}" not in h), key=lambda x: x["id"])


def spot_due(count_after):
    """count_after is the number of replacements including this one."""
    return count_after % SPOT_EVERY == 1 or SPOT_EVERY == 1


# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return None


def save_state(st):
    st["handled"] = st["handled"][-KEEP_HANDLED:]
    if st.get("tv"):
        st["tv"]["handled"] = st["tv"]["handled"][-KEEP_HANDLED:]
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE_FILE)


def mark(st, row):
    if row_key(row) not in st["handled"]:
        st["handled"].append(row_key(row))


# ---------------------------------------------------------------- side effects
def probe_audio(video):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream_tags=language,title",
                        "-of", "json", video], capture_output=True, text=True, timeout=60)
    return p.stdout if p.returncode == 0 else ""


def method_of(title, since_iso):
    """How the accepted sub was found, and the backup path, read from the replace tool's ledger."""
    how, backup = "unknown", ""
    for l in open(r.LEDGER):
        e = json.loads(l)
        if e.get("title") != title or e.get("ts", "") < since_iso:
            continue
        if e.get("event") == "start":
            backup = e.get("backup", "")
        if e.get("event") == "candidate" and e.get("state") == "PASS":
            if e.get("via") == "shifted":
                how = f"{e.get('provider')} via Bazarr, shifted {e.get('delta', 0):+.2f} s"
            elif e.get("path") == "direct-shifted":
                how = f"subsource, shifted {e.get('delta', 0):+.2f} s"
            elif e.get("path") == "direct":
                how = "subsource, as downloaded"
            else:
                how = "whisperai (transcribed from the audio)" if e.get("provider") == "whisperai" else f"{e.get('provider')} via Bazarr"
    return how, backup


DIGEST = None      # a list while the backlog lane runs: its messages are collected for the daily digest instead of posted


def digest_entry(msg):
    """A message, as a digest record: {"kind": replaced|unresolved|review, "title", "how", "text"}."""
    lines = msg.split("\n")
    how = next((x[4:].strip() for x in lines if x.startswith("How:")), "")
    m = re.match(r"\*\*Subtitle (replaced|unresolved)(?: \(TV\))?:\*\* (.+?)(?:: no candidate.*)?$", lines[0])
    if m:
        return {"kind": m.group(1), "title": m.group(2), "how": how, "text": lines[0].replace("**", "")}
    return {"kind": "review", "title": "", "how": "", "text": lines[0].replace("**", "")}


def margin(offsets):
    """How loose a pass was: the largest window offset after setting aside the one stray window. None when there are no offsets."""
    a = sorted((abs(o) for o in offsets if o is not None), reverse=True)
    return None if not a else (a[1] if len(a) > 1 else a[0])


def say(msg, quiet):
    log(msg.replace("\n", " | "))
    if DIGEST is not None and ", failed:" not in msg.split("\n")[0]:
        if "tranche done" not in msg:
            DIGEST.append(digest_entry(msg))
        return
    if not quiet:
        gate.discord(msg)


def do_replace(st, m, key):
    """Replace one movie's sub through the replace tool and report it. Returns "guard" (Plex guard stopped it; stays queued),
    "done" (replaced or unresolved, posted), "noop" (the sub no longer fails; nothing posted) or "error"."""
    started = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        outcome = r.process(m, False, True)
    except SystemExit as e:                       # the Plex guard: stop the whole tick, retry next time
        log(f"stopped by guard: {str(e)[:120]}; {m['title']} stays queued")
        return "guard"
    except Exception as e:
        st["attempts"][key] = st["attempts"].get(key, 0) + 1
        log(f"{m['title']}: error {type(e).__name__}: {str(e)[:120]}")
        if st["attempts"][key] >= MAX_ATTEMPTS:
            say(f"**Subtitle check, failed:** {m['title']}: the replace step errored twice. The original is untouched or "
                f"restored. Needs a look.", False)
            return "noop"
        return "error"
    if outcome is None:
        log(f"{m['title']}: no longer a FAIL (or could not be measured); nothing to do")
        return "noop"
    st["last_outcome"] = outcome
    how, backup = method_of(m["title"], started)
    if outcome == "replaced":
        st["replaced"] += 1
        spot = spot_due(st["replaced"]) and DIGEST is None
        if spot:
            st["spot"].append(m["title"])
        say(f"**Subtitle replaced:** {m['title']}\nHow: {how}\nBackup: `{backup}`\nUndo: `subtitle-replace.py --restore \"{m['title']}\"`"
            + ("\n:warning: Transcribed from the audio, so names and made-up words may be wrong. The timing passed the check. The original is in the backup." if "whisperai" in how else "")
            + ("\n:eyes: **Spot-check this one** when you can, then `subtitle-enforce.py --spot \"" + m["title"] + "\" good|bad`." if spot else ""), False)
    else:
        say(f"**Subtitle unresolved:** {m['title']}: no candidate passed the check. Backup: `{backup}`. "
            f"Tier B keeps the old file; a Tier A fragment was removed and can be restored with `--restore`.", False)
    r.ledger({"title": m["title"], "event": "enforce", "outcome": outcome, "how": how})
    return "done"


# ---------------------------------------------------------------- TV episodes
def new_tv_state(since_id):
    return {"enabled": True, "since_id": since_id, "handled": [], "replaced": 0, "attempts": {}, "spot": [], "day": "", "day_count": 0}


def tv_rows(since_id):
    """New English episode downloads in Bazarr's history after since_id that have settled on disk."""
    now, out = time.time(), []
    for hid, eid, vid, sub_c in r.ro_db().execute(
            "select id,sonarrEpisodeId,video_path,subtitles_path from table_history where id>? and action in (1,2,3) "
            "and language in ('en','en:hi') order by id", (since_id,)):
        sub = gate.host_path(sub_c)
        if not (sub and os.path.isfile(sub)):
            continue
        if now - os.stat(sub).st_mtime < gate.SETTLE_SECONDS or os.path.exists(sub[:-4] + ".synced.srt"):
            continue
        out.append({"id": hid, "eid": eid, "path": sub, "fp": current_fp(sub)})
    return out


def do_replace_tv(tvs, tv, m, key):
    """Replace one episode's sub with the TV tool and report it. Returns "guard", "done", "noop" or "error" (as do_replace)."""
    started = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        outcome = tv.process(m, False)
    except SystemExit as e:
        log(f"stopped by guard: {str(e)[:120]}; {m['title']} stays queued")
        return "guard"
    except Exception as e:
        tvs["attempts"][key] = tvs["attempts"].get(key, 0) + 1
        log(f"{m['title']}: error {type(e).__name__}: {str(e)[:120]}")
        if tvs["attempts"][key] >= MAX_ATTEMPTS:
            say(f"**Subtitle check (TV), failed:** {m['title']}: the replace step errored twice. The original is untouched or restored. Needs a look.", False)
            return "noop"
        return "error"
    if outcome is None:
        log(f"{m['title']}: no longer a FAIL (or could not be measured); nothing to do")
        return "noop"
    how, backup = method_of(m["title"], started)
    if outcome == "replaced":
        tvs["replaced"] += 1
        tvs["day_count"] += 1
        spot = spot_due(tvs["replaced"]) and DIGEST is None
        if spot:
            tvs["spot"].append(m["title"])
        say(f"**Subtitle replaced (TV):** {m['title']}\nHow: {how}\nBackup: `{backup}`\nUndo: `subtitle-replace-tv.py --restore \"{m['title']}\"`"
            + ("\n:warning: Transcribed from the audio, so names and made-up words may be wrong. The timing passed the check. The original is in the backup." if "whisperai" in how else "")
            + ("\n:eyes: **Spot-check this one** when you can, then `subtitle-enforce.py --spot \"" + m["title"] + "\" good|bad`." if spot else ""), False)
    else:
        say(f"**Subtitle unresolved (TV):** {m['title']}: no candidate passed the check, so the original is kept. Backup: `{backup}`.", False)
    r.ledger({"title": m["title"], "kind": "episode", "event": "enforce", "outcome": outcome, "how": how})
    return "done"


def tv_core(st, slot, tvs, tv, work, dry, cap_measure, cap_replace, daily_cap, budget_s):
    """Work through rows [{"eid","path","fp"[,"key"]}]: measure each episode subtitle, replace the FAILs. Used for new downloads (slot "tv")
    and for the backlog (slot "backlog_tv"); tvs is that slot's state. Returns (measured, replaced). dry = nothing replaced, posted or saved."""
    today = datetime.date.today().isoformat()
    if tvs["day"] != today:
        tvs["day"], tvs["day_count"] = today, 0
    t0, measured, replaced = time.time(), 0, 0
    for row in work:
        if time.time() - t0 > budget_s or measured >= cap_measure:
            log("TV: per-pass cap reached; the rest wait"); break
        if not dry and os.path.exists(PAUSE):
            break
        if not dry and tvs["day_count"] >= daily_cap:
            log("TV: daily replacement cap reached"); break
        key = row.get("key") or f"{row['path']}|{row['fp']}"
        try:
            m = tv.find_episode_by_id(row["eid"])
        except SystemExit as e:
            log(f"TV: episode {row['eid']}: {str(e)[:100]}; dropped"); tvs["handled"].append(key); continue
        if row.get("path") and (m["sub"] != row["path"] or current_fp(m["sub"]) != row["fp"]):
            log(f"TV: {m['title']}: the sub changed since the download; dropped"); tvs["handled"].append(key); continue
        tv.CURRENT_SHOW, tv.CURRENT_VIDEO = m["show"], os.path.basename(m["video"])
        try:
            tv.guard()
        except SystemExit as e:
            if str(e).startswith(tv.THIS_SHOW_MSG):
                log(f"TV: {m['title']}: show is being watched; stays queued"); continue
            log(f"TV: {str(e)[:100]}; TV work stops for this pass"); break
        ok, note = audio_ok(probe_audio(m["video"]))
        if not ok:
            say(f"**Subtitle check (TV), needs review:** {m['title']}: {note}. Not measured, not replaced.", dry)
            tvs["handled"].append(key); continue
        measured += 1
        try:
            rec, state, why = tv.measure_one(m["sub_c"], m["video_c"])
        except Exception as e:
            log(f"TV: {m['title']}: measuring failed ({type(e).__name__}); stays queued"); continue
        if rec is None:
            log(f"TV: {m['title']}: could not be measured; stays queued"); continue
        action = classify_tv(state, why)
        log(f"TV: {m['title']}: {state} {why[:2]} -> {action}")
        if action == "ignore":
            tvs["handled"].append(key); continue
        if action == "review-coverage":
            say(f"**Subtitle check (TV), needs review:** {m['title']}: the only problem is where the last cue ends ({why[0]}). Not replaced automatically.", dry)
            tvs["handled"].append(key); continue
        if dry:
            log(f"TV: WOULD replace {m['title']}"); continue
        if replaced >= cap_replace:
            log("TV: replacement cap reached; this one waits"); break
        res = do_replace_tv(tvs, tv, m, key)
        if res == "guard":
            continue
        if res == "done":
            replaced += 1
            try:                                 # the file now in place was vetted by the replace tool; do not measure it again
                m2 = tv.find_episode_by_id(row["eid"])
                tvs["handled"].append(f"{m2['sub']}|{current_fp(m2['sub'])}")
            except SystemExit:
                pass
        if res in ("done", "noop"):
            tvs["handled"].append(key)
        st[slot] = tvs; save_state(st)
    if not dry:
        st[slot] = tvs
    return measured, replaced


def tv_step(st, dry, look_back_h=0):
    """New episode downloads: measure and replace the FAILs. dry = measure and report only (nothing replaced, posted or saved)."""
    tvs = st.get("tv")
    if dry and look_back_h:
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=look_back_h)).strftime("%Y-%m-%d %H:%M:%S")
        first = r.ro_db().execute("select min(id) from table_history where timestamp>=?", (cutoff,)).fetchone()[0]
        tvs = new_tv_state((first - 1) if first else 10**12)
    elif not tvs or not tvs.get("enabled"):
        return
    try:
        tv = _load("replace_tv", "subtitle-replace-tv.py")
    except Exception as e:
        return log(f"TV: could not load subtitle-replace-tv.py ({type(e).__name__}); skipped")
    work = plan_tv_work(tv_rows(tvs["since_id"]), tvs["handled"])
    log(f"TV: {len(work)} new episode subtitles to look at")
    measured, replaced = tv_core(st, "tv", tvs, tv, work, dry, TV_CAP_MEASURE, TV_CAP_REPLACE, TV_DAILY_REPLACE, TV_BUDGET_S)
    log(f"TV: step done: {measured} measured, {replaced} replaced")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\s*\(\d{4}\)\s*$", "", (s or "").lower()))


def order_tv_backlog(items, watch, done):
    """items: [{"eid","show",...}] in library order; watch: show names, most recently watched first; done: set of keys already handled.
    Shows Jay watches come first (most recent first), everything else after in library order. A show is never split."""
    rank = {}
    for i, name in enumerate(watch):
        rank.setdefault(_norm(name), i)
    far = len(rank) + 1
    todo = [x for x in items if f"eid:{x['eid']}" not in done]
    return sorted(todo, key=lambda x: rank.get(_norm(x["show"]), far))     # stable: library order is kept inside a rank


def tv_backlog_items():
    return [{"eid": e, "show": t, "season": se, "episode": ep} for e, t, se, ep in r.ro_db().execute(
        "select e.sonarrEpisodeId, sh.title, e.season, e.episode from table_episodes e "
        "join table_shows sh on sh.sonarrSeriesId=e.sonarrSeriesId order by sh.sonarrSeriesId, e.season, e.episode")]


def _watch_cmd_tv():
    return WATCH_CMD.replace('x.get(\\"type\\")==\\"movie\\"', 'x.get(\\"type\\")==\\"episode\\"').replace(
        'x.get(\\"title\\",\\"\\"), x.get(\\"year\\",\\"\\")', 'x.get(\\"grandparentTitle\\",\\"\\"), \\"\\"')


def watch_shows():
    """Shows Jay (Plex account 1) has watched, most recently watched first. Empty list if Plex cannot be asked."""
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jay@192.168.0.22", _watch_cmd_tv()],
                             capture_output=True, text=True, timeout=60).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    seen, res = set(), []
    for line in out.splitlines():
        t = line.partition("\t")[0]
        if t and t not in seen:
            seen.add(t); res.append(t)
    return res


def enable_backlog(on):
    st = load_state() or {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
    if on:
        st["backlog"] = {"enabled": True, "digest": [], "digest_day": "", "measured_tv": 0, "since": datetime.datetime.now().isoformat(timespec="seconds")}
        st.setdefault("backlog_tv", new_tv_state(0))
        save_state(st)
        print("backlog lane ON: after new downloads each tick it works through old TV episodes and movies (watched shows first)")
    else:
        if st.get("backlog"):
            st["backlog"]["enabled"] = False
        save_state(st)
        print("backlog lane OFF")


def post_digest(st, force=False):
    bl = st["backlog"]
    today = datetime.date.today().isoformat()
    if not force and (bl.get("digest_day") == today or datetime.datetime.now().hour < BL_DIGEST_HOUR):
        return
    ent, since = bl.get("digest", []), bl.get("since_digest") or bl["since"]
    bl["digest_day"], bl["digest"], bl["since_digest"] = today, [], datetime.datetime.now().isoformat(timespec="seconds")
    rep_ = [e for e in ent if e["kind"] == "replaced"]
    if not ent and not bl.get("measured_tv") and not bl.get("measured_movies"):
        return
    ranked = []
    for e in rep_:                      # the loosest passes, from the ledger offsets of the accepted candidate
        best = None
        with open(r.LEDGER) as fh:
            for l in fh:
                x = json.loads(l)
                if x.get("title") == e["title"] and x.get("event") == "candidate" and x.get("state") == "PASS" and x.get("offsets") and x.get("ts", "") >= since:
                    best = margin(x["offsets"])
        ranked.append((best if best is not None else -1, e))
    ranked.sort(key=lambda t: -t[0])
    weakest = [e for mg, e in ranked[:BL_SPOT_N] if mg >= 0]
    tvb, tvl = st.get("backlog_tv", {}), st.setdefault("backlog_tv", new_tv_state(0)).setdefault("spot", [])
    for e in weakest:
        lst = tvl if re.search(r"S\d+E\d+", e["title"]) else st["spot"]
        if e["title"] not in lst:
            lst.append(e["title"])
    unres = [e for e in ent if e["kind"] == "unresolved"]
    rev = [e for e in ent if e["kind"] == "review"]
    lines = [f"**Subtitle backlog, digest {today}:** measured {bl.get('measured_tv', 0)} TV episodes and {bl.get('measured_movies', 0)} movies since the last digest; "
             f"replaced {len(rep_)}, unresolved {len(unres)}, needs review {len(rev)}."]
    if weakest:
        lines.append("Looser passes to eyeball (then `subtitle-enforce.py --spot \"TITLE\" good|bad`): " + "; ".join(
            f"{e['title']} ({e['how'] or 'n/a'})" for e in weakest))
    if any("whisperai" in e["how"] for e in rep_):
        lines.append(f":warning: {sum(1 for e in rep_ if 'whisperai' in e['how'])} of the replacements are transcriptions from the audio: timing passed the check, names may be misheard.")
    if unres:
        lines.append("Unresolved (original kept): " + "; ".join(e["title"] for e in unres[:12]) + (f" and {len(unres) - 12} more" if len(unres) > 12 else ""))
    if rev:                              # the same reason across many episodes of one show is one line with a count
        groups = {}
        for e in rev:
            k = re.sub(r"S\d+E\d+", "SxxExx", e["text"]); groups[k] = groups.get(k, 0) + 1
        lines.append("Needs review: " + "; ".join(f"{k}" + (f" (x{n})" if n > 1 else "") for k, n in list(groups.items())[:8]) + (f" and {len(groups) - 8} more" if len(groups) > 8 else ""))
    bl["measured_tv"] = bl["measured_movies"] = 0
    say("\n".join(lines), False)


def backlog_lane(st, dry, t_tick):
    """Old library content, after the new-download work. TV for 5 minutes (watched shows first), then movies for 5 minutes."""
    global DIGEST
    bl = st.get("backlog")
    if dry or not bl or not bl.get("enabled") or os.path.exists(PAUSE):
        return
    if time.time() - t_tick > BL_MAX_TICK_ELAPSED:
        return log("backlog: this tick already ran long; skipped")
    tvb = st.setdefault("backlog_tv", new_tv_state(0))
    today = datetime.date.today().isoformat()
    if tvb.get("day") != today:
        tvb["day"], tvb["day_count"] = today, 0
    if tvb["day_count"] >= BL_DAILY_REPLACE:
        log("backlog: daily replacement cap reached"); post_digest(st); return
    DIGEST = []
    try:
        try:
            tv = _load("replace_tv", "subtitle-replace-tv.py")
            ordered = order_tv_backlog(tv_backlog_items(), watch_shows(), set(tvb["handled"]))
            work = [{"eid": x["eid"], "path": None, "fp": None, "key": f"eid:{x['eid']}"} for x in ordered[:40]]
            log(f"backlog: {len(ordered)} TV episodes still to look at")
            m1, _ = tv_core(st, "backlog_tv", tvb, tv, work, False, 40, BL_PASS_REPLACE, BL_DAILY_REPLACE, BL_TV_SECONDS)
            bl["measured_tv"] = bl.get("measured_tv", 0) + m1
        except Exception as e:
            log(f"backlog: TV pass failed ({type(e).__name__}: {str(e)[:100]}); continuing")
        left = BL_DAILY_REPLACE - tvb["day_count"]
        if left > 0 and not os.path.exists(PAUSE):
            before = st["replaced"]
            try:
                tally = sweep(12, BL_MOVIE_MINUTES, st=st, max_replace=min(BL_PASS_REPLACE, left))
            except Exception as e:
                tally = None; log(f"backlog: movie pass failed ({type(e).__name__}: {str(e)[:100]}); continuing")
            tvb["day_count"] += st["replaced"] - before
            if tally:
                bl["measured_movies"] = bl.get("measured_movies", 0) + sum(v for k, v in tally.items() if k in ("PASS", "UNVERIFIABLE", "needs review", "replaced", "unresolved"))
    finally:
        got, DIGEST = DIGEST, None
    bl["digest"] = bl.get("digest", []) + got
    post_digest(st)


def backlog_dry_run():
    ordered = order_tv_backlog(tv_backlog_items(), watch_shows(), set((load_state() or {}).get("backlog_tv", {}).get("handled", [])))
    print(f"{len(ordered)} TV episodes in the queue. First ten:")
    for x in ordered[:10]:
        print(f"  {x['show']} S{x['season']:02d}E{x['episode']:02d}")
    lk = open(r.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another subtitle run holds the lock; not measuring")
    tv = _load("replace_tv", "subtitle-replace-tv.py")
    tvb = new_tv_state(0)
    work = [{"eid": x["eid"], "path": None, "fp": None, "key": f"eid:{x['eid']}"} for x in ordered[:3]]
    m1, _ = tv_core({}, "backlog_tv", tvb, tv, work, True, 3, 0, 0, 600)
    print(f"measured {m1} (nothing replaced, posted or saved)")


def enable_tv(on):
    st = load_state() or {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
    if on:
        mx = r.ro_db().execute("select coalesce(max(id),0) from table_history").fetchone()[0]
        st["tv"] = new_tv_state(mx)
        save_state(st)
        print(f"TV enforcement ON: acts on English episode downloads after Bazarr history id {mx}")
    else:
        if st.get("tv"):
            st["tv"]["enabled"] = False
        save_state(st)
        print("TV enforcement OFF")


def tv_dry_run(hours):
    lk = open(r.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another subtitle run holds the lock")
    tv_step(load_state() or {}, True, hours)


def test_title(title):
    """Run one title through exactly the code path a tick uses, on Jay's say-so (for the first end-to-end check)."""
    if os.path.exists(PAUSE):
        sys.exit("PAUSED; resume first")
    lk = open(r.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("another subtitle run holds the lock")
    st = load_state() or {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
    m = r.find_movie(title)
    ok, note = audio_ok(probe_audio(m["video"]))
    if not ok:
        return say(f"**Subtitle check, needs review:** {m['title']}: {note}. Not replaced.", False)
    res = do_replace(st, m, f"test|{m['title']}")
    save_state(st)
    log(f"test-title result: {res}")


def order_candidates(movies, watch, done):
    """Movies still to measure, watch-list titles first (most recently watched first), then the rest in library order.
    movies: [{"rid","title","year"}]; watch: [(title, year or None)] newest first; done: set of titles already handled.
    A watch title that matches more than one movie (same title, different year, no year given) is skipped there and
    comes up later in library order, so it is never matched to the wrong film."""
    todo = [m for m in movies if m["title"] not in done]
    by_title = {}
    for m in todo:
        by_title.setdefault(m["title"].lower(), []).append(m)
    first, taken = [], set()
    for t, y in watch:
        hits = by_title.get(t.lower(), [])
        if y:
            hits = [m for m in hits if str(m.get("year")) == str(y)] or ([] if len(hits) > 1 else hits)
        if len(hits) == 1 and hits[0]["rid"] not in taken:
            first.append(hits[0]); taken.add(hits[0]["rid"])
    return first + [m for m in todo if m["rid"] not in taken]


def tick(dry, look_back_h=0):
    os.makedirs(ROOT, exist_ok=True)
    t_tick = time.time()
    if os.path.exists(PAUSE):
        return log("PAUSED (" + (open(PAUSE).read().strip()[:120] or "no reason given") + "); nothing done")
    lk = open(r.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another subtitle run holds the lock; skipping this tick")
    r.recover()
    st = load_state()
    if st is None:
        st = {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
        if not dry:
            save_state(st)
        log("first tick: only gate results from now on are acted on")
    if not dry:
        try:
            subprocess.run([sys.executable, os.path.join(HERE, "subtitle-gate.py"), "--no-discord", "--max-minutes", str(GATE_MINUTES)]
                           + (["--movies-only"] if (st.get("tv") or {}).get("enabled") else []),
                           timeout=(GATE_MINUTES + 10) * 60, capture_output=True)
        except subprocess.TimeoutExpired:
            log("gate run timed out; continuing with what it recorded")
    since = time.time() - look_back_h * 3600 if (dry and look_back_h) else st["since_ts"]
    work = plan_work(gate.read_ledger(), st, since)
    done, tv_flags = 0, []
    for action, row in work:
        name = gate.nice_name(row["path"])
        if action == "ignore":
            if row.get("table") == "episodes" and row["state"] == "FAIL":
                tv_flags.append(f"- {name} ({row['provider']}): {'; '.join(row['reasons'][:2])}")
            if not dry:
                mark(st, row)
            continue
        if action == "review-coverage":
            say(f"**Subtitle check, needs review:** {name}: the only problem is where the last cue ends ({row['reasons'][0]}). "
                f"That can be long credits, so it is not replaced automatically.", dry)
            if not dry:
                mark(st, row)
            continue
        if done >= CAP:
            log(f"cap of {CAP} per tick reached; {name} waits for the next tick"); continue
        m = r.movie_by_history_id(row["history_id"])
        if m is None or m["sub"] != row["path"] or current_fp(m["sub"]) != row["fp"]:
            log(f"{name}: the sub changed or the movie is gone since the gate measured it; dropped")
            if not dry:
                mark(st, row)
            continue
        ok, note = audio_ok(probe_audio(m["video"]))
        if not ok:
            say(f"**Subtitle check, needs review:** {m['title']}: {note}. Not replaced.", dry)
            if not dry:
                mark(st, row)
            continue
        if dry:
            log(f"WOULD replace: {m['title']} ({'; '.join(row['reasons'][:2])})"); done += 1; continue
        res = do_replace(st, m, row_key(row))
        if res == "guard":
            break
        if res == "done":
            done += 1
        if res in ("done", "noop"):
            mark(st, row)
        save_state(st)
    tv_step(st, dry)
    backlog_lane(st, dry, t_tick)
    if tv_flags and not (st.get("tv") or {}).get("enabled"):
        say(f"**Subtitle gate (TV, report-only):** {len(tv_flags)} new episode subs flagged; nothing was changed.\n" + "\n".join(tv_flags[:10]), dry)
    if not dry:
        save_state(st)
    log(f"tick done: {done} handled, {len(work)} rows considered")


WATCH_CMD = ('T=$(sudo -n grep -o "PlexOnlineToken=\\"[^\\"]*\\"" "/srv/docker/plex/Library/Application Support/Plex Media Server/'
             'Preferences.xml" | cut -d\\" -f2); curl -s -m 30 -H "Accept: application/json" '
             '"http://127.0.0.1:32400/status/sessions/history/all?accountID=1&sort=viewedAt:desc&X-Plex-Container-Size=500&X-Plex-Token=$T" '
             '| python3 -c "import json,sys; [print(x.get(\\"title\\",\\"\\"), x.get(\\"year\\",\\"\\"), sep=chr(9)) for x in json.load(sys.stdin)[\\"MediaContainer\\"].get(\\"Metadata\\",[]) if x.get(\\"type\\")==\\"movie\\"]"')


def watch_list():
    """Distinct movies Jay (Plex account 1) has watched, newest first. Empty list if Plex cannot be asked."""
    try:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "jay@192.168.0.22", WATCH_CMD],
                             capture_output=True, text=True, timeout=60).stdout
    except (subprocess.TimeoutExpired, OSError):
        return []
    seen, res = set(), []
    for line in out.splitlines():
        t, _, y = line.partition("\t")
        if t and t not in seen:
            seen.add(t); res.append((t, y.strip() or None))
    return res


def handled_titles():
    """Titles already measured or fixed by any earlier survey, sweep, T0 or enforce run."""
    done = set()
    if os.path.exists(r.LEDGER):
        for l in open(r.LEDGER):
            e = json.loads(l)
            if e.get("event") in ("survey", "sweep", "enforce", "start", "review"):
                done.add(e.get("title"))
    return done


def sweep(n, max_minutes, st=None, max_replace=None):
    """Measure the next n library movies (watch list first) and replace the FAILs through the same path a tick uses.
    By hand: a tranche with Jay's go. From the backlog lane: st is the tick's state and the tick already holds the lock."""
    os.makedirs(ROOT, exist_ok=True)
    if os.path.exists(PAUSE):
        return log("PAUSED; nothing done")
    if st is None:
        lk = open(r.LOCK, "w")
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return log("another subtitle run holds the lock")
        r.recover()
        st = load_state() or {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
    db = r.ro_db()
    movies = [{"rid": a, "title": b, "year": c} for a, b, c in db.execute(
        "select radarrId, title, year from table_movies where radarrId in (select radarrId from table_history_movie "
        "where action in (1,2,3) and language in ('en','en:hi')) order by radarrId")]
    queue = order_candidates(movies, watch_list(), handled_titles())
    log(f"sweep: {len(queue)} movies not yet measured; this tranche is up to {n}")
    tally = {"PASS": 0, "UNVERIFIABLE": 0, "needs review": 0, "replaced": 0, "unresolved": 0, "skipped": 0, "errors": 0}
    started, stopped = time.time(), ""
    for mv in queue:
        if sum(tally[k] for k in tally if k != "skipped") >= n or time.time() - started > max_minutes * 60:
            break
        if os.path.exists(PAUSE):
            stopped = "PAUSE file appeared"; break
        if max_replace is not None and tally["replaced"] + tally["unresolved"] >= max_replace:
            stopped = "replacement cap for this pass"; break
        try:
            row = r.ro_db().execute("select radarrId,title,path from table_movies where radarrId=?", (mv["rid"],)).fetchone()
            m = r._movie(*row)
            r.CURRENT_VIDEO = os.path.basename(m["video"])
            try:
                r.guard()                      # per title: only a stream of THIS movie blocks it
            except SystemExit as e:
                if str(e).startswith(r.THIS_MOVIE_MSG):
                    tally["skipped"] += 1; log(f"{m['title']}: being watched right now; left for a later tranche"); continue
                raise
            if not (os.path.isfile(m["sub"]) and os.path.isfile(m["video"])):
                tally["skipped"] += 1; r.ledger({"title": m["title"], "event": "sweep", "state": "missing-file"}); continue
            rec, state, why = r.measure_one(m["sub_c"], m["video_c"])
        except SystemExit as e:
            stopped = str(e)[:100]; break
        except Exception as e:
            tally["errors"] += 1; log(f"{mv['title']}: error {type(e).__name__}: {str(e)[:100]}"); continue
        if rec is None:
            tally["skipped"] += 1; continue
        fake = {"table": "movies", "state": state, "reasons": why}
        action = classify(fake)
        r.ledger({"title": m["title"], "event": "sweep", "state": state, "why": why})
        if action == "ignore":
            tally["PASS" if state == "PASS" else "UNVERIFIABLE"] += 1
            log(f"{m['title']}: {state}"); continue
        if action == "review-coverage":
            tally["needs review"] += 1
            say(f"**Subtitle sweep, needs review:** {m['title']}: the only problem is where the last cue ends ({why[0]}).", False); continue
        ok, note = audio_ok(probe_audio(m["video"]))
        if not ok:
            tally["needs review"] += 1
            say(f"**Subtitle sweep, needs review:** {m['title']}: {note}. Not replaced.", False); continue
        res = do_replace(st, m, f"sweep|{m['title']}")
        save_state(st)
        if res == "guard":
            stopped = "movie stream started"; break
        if res == "done":
            tally["replaced" if st.get("last_outcome") == "replaced" else "unresolved"] += 1
        elif res == "error":
            tally["errors"] += 1
    summary = ", ".join(f"{k} {v}" for k, v in tally.items() if v)
    if not summary:
        return log(f"sweep: nothing measured" + (f" ({stopped})" if stopped else ""))
    say(f"**Subtitle sweep tranche done:** {summary or 'nothing measured'}." + (f" Stopped early: {stopped}." if stopped else "")
        + f" {max(len(queue) - sum(tally.values()), 0)} movies still to measure.", False)
    return tally


def status():
    st = load_state()
    print("PAUSED: " + open(PAUSE).read().strip() if os.path.exists(PAUSE) else "not paused")
    if not st:
        return print("no enforce state yet (the first tick sets the go-live time)")
    print(f"live since {datetime.datetime.fromtimestamp(st['since_ts']):%Y-%m-%d %H:%M}; replacements {st['replaced']}; "
          f"handled rows {len(st['handled'])}; spot-checks waiting: {st['spot'] or 'none'}")
    b, tb = st.get("backlog"), st.get("backlog_tv", {})
    print("backlog lane: " + (f"ON since {b['since']}; TV episodes handled {len(tb.get('handled', []))}; replaced today {tb.get('day_count', 0)}/{BL_DAILY_REPLACE}; digest entries waiting {len(b.get('digest', []))}; spot-checks waiting: {tb.get('spot') or 'none'}" if b and b.get("enabled") else "off"))
    t = st.get("tv")
    print("TV: " + (f"ON since history id {t['since_id']}; replaced {t['replaced']} ({t['day_count']} today); spot-checks waiting: {t['spot'] or 'none'}" if t and t.get("enabled") else "off"))


def spot(title, verdict):
    if verdict not in ("good", "bad"):
        sys.exit("verdict must be good or bad")
    st = load_state()
    if not st:
        sys.exit("no enforce state yet")
    lists = [st["spot"], (st.get("tv") or {}).get("spot", []), (st.get("backlog_tv") or {}).get("spot", [])]
    hits = [t for l in lists for t in l if title.lower() in t.lower()]
    if len(hits) != 1:
        sys.exit(f"{len(hits)} waiting spot-checks match {title!r}: {hits}")
    next(l for l in lists if hits[0] in l).remove(hits[0]); save_state(st)
    r.ledger({"title": hits[0], "event": "spot-check", "by": "Jay", "verdict": verdict})
    print(f"recorded {verdict}: {hits[0]}")
    if verdict == "bad":
        open(PAUSE, "w").write(f"spot-check of {hits[0]} was bad ({datetime.datetime.now():%Y-%m-%d %H:%M}); report-only until Jay resumes")
        gate.discord(f"**Subtitle enforce PAUSED:** the spot-check of {hits[0]} was bad. The gate stays report-only until Jay resumes it.")
        print("PAUSE written: enforce mode is off until `--resume`")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--look-back-hours", type=float, default=0, help="with --dry-run only: pretend go-live was this long ago")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--spot", nargs=2, metavar=("TITLE", "VERDICT")); ap.add_argument("--resume", action="store_true"); ap.add_argument("--enable-tv", action="store_true"); ap.add_argument("--enable-backlog", action="store_true"); ap.add_argument("--disable-backlog", action="store_true"); ap.add_argument("--backlog-dry-run", action="store_true"); ap.add_argument("--digest-now", action="store_true"); ap.add_argument("--disable-tv", action="store_true"); ap.add_argument("--tv-dry-run", type=float, metavar="HOURS"); ap.add_argument("--sweep", type=int, metavar="N", help="measure the next N library movies (watch list first) and fix the FAILs"); ap.add_argument("--sweep-minutes", type=int, default=300); ap.add_argument("--test-title", metavar="TITLE", help="run one movie through the real replace-and-report path now"); ap.add_argument("--test-post", action="store_true", help="send one clearly-labelled TEST message to Discord")
    a = ap.parse_args()
    if a.enable_backlog or a.disable_backlog:
        return enable_backlog(a.enable_backlog)
    if a.backlog_dry_run:
        return backlog_dry_run()
    if a.digest_now:
        st = load_state(); post_digest(st, True); return save_state(st)
    if a.enable_tv or a.disable_tv:
        return enable_tv(a.enable_tv)
    if a.tv_dry_run:
        return tv_dry_run(a.tv_dry_run)
    if a.sweep:
        return sweep(a.sweep, a.sweep_minutes)
    if a.test_title:
        return test_title(a.test_title)
    if a.test_post:
        gate.discord("**TEST (no action taken):** subtitle enforce wrapper, Discord check. Real posts look like this:\n"
                     "**Subtitle replaced:** Example Movie\nHow: subsource, shifted +0.88 s\nBackup: `~/subtitle-sweep/backups/DATE/Example Movie`\n"
                     "Undo: `subtitle-replace.py --restore \"Example Movie\"`\n:eyes: **Spot-check this one** when you can.")
        return print("test message sent")
    if a.status:
        return status()
    if a.spot:
        return spot(*a.spot)
    if a.resume:
        if os.path.exists(PAUSE):
            os.remove(PAUSE); return print("PAUSE removed")
        return print("was not paused")
    tick(a.dry_run, a.look_back_hours)


if __name__ == "__main__":
    main()
