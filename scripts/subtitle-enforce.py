#!/usr/bin/env python3
"""Subtitle enforce wrapper (issue #73, docs/subtitle-enforce-mode.md). MOVIES ONLY.

One tick (cron every 15 minutes, once approved for go-live):
  1. stop if ~/subtitle-sweep/PAUSE exists, or if another replace run holds the lock
  2. run the gate (measure new downloads; report-only, silent on Discord)
  3. for each NEW movie FAIL: replace it with subtitle-replace.py's process() (cap per tick)
  4. post to Discord for every replacement, every unresolved title, every "needs review" title

Never replaced, only reported as "needs review": coverage-only FAILs, a first audio stream that is not English or is a
commentary track. UNVERIFIABLE and TV episodes are ignored. Spanish files are never touched.

  subtitle-enforce.py               one tick
  subtitle-enforce.py --dry-run     show what a tick WOULD do; runs no gate, replaces nothing, posts nothing
  subtitle-enforce.py --status      counters, pause state, spot-checks waiting
  subtitle-enforce.py --spot TITLE good|bad    record Jay's eyes-on check of a replacement (bad = writes PAUSE)
  subtitle-enforce.py --resume      remove PAUSE (Jay's decision only)
"""
import argparse, datetime, fcntl, importlib.util, json, os, subprocess, sys, time

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
            if e.get("path") == "direct-shifted":
                how = f"subsource, shifted {e.get('delta', 0):+.2f} s"
            elif e.get("path") == "direct":
                how = "subsource, as downloaded"
            else:
                how = "whisperai (transcribed from the audio)" if e.get("provider") == "whisperai" else f"{e.get('provider')} via Bazarr"
    return how, backup


def say(msg, quiet):
    log(msg.replace("\n", " | "))
    if not quiet:
        gate.discord(msg)


def tick(dry, look_back_h=0):
    os.makedirs(ROOT, exist_ok=True)
    if os.path.exists(PAUSE):
        return log("PAUSED (" + (open(PAUSE).read().strip()[:120] or "no reason given") + "); nothing done")
    lk = open(r.LOCK, "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return log("another subtitle run holds the lock; skipping this tick")
    st = load_state()
    if st is None:
        st = {"since_ts": time.time(), "handled": [], "replaced": 0, "attempts": {}, "spot": []}
        if not dry:
            save_state(st)
        log("first tick: only gate results from now on are acted on")
    if not dry:
        try:
            subprocess.run([sys.executable, os.path.join(HERE, "subtitle-gate.py"), "--no-discord", "--max-minutes", str(GATE_MINUTES)],
                           timeout=(GATE_MINUTES + 10) * 60, capture_output=True)
        except subprocess.TimeoutExpired:
            log("gate run timed out; continuing with what it recorded")
    since = time.time() - look_back_h * 3600 if (dry and look_back_h) else st["since_ts"]
    work = plan_work(gate.read_ledger(), st, since)
    done = 0
    for action, row in work:
        name = gate.nice_name(row["path"])
        if action == "ignore":
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
        started = datetime.datetime.now().isoformat(timespec="seconds")
        key = row_key(row)
        try:
            outcome = r.process(m, False, True)
        except SystemExit as e:                   # the Plex guard: stop the whole tick, retry next time
            log(f"stopped by guard: {str(e)[:120]}; {m['title']} stays queued")
            break
        except Exception as e:
            st["attempts"][key] = st["attempts"].get(key, 0) + 1
            log(f"{m['title']}: error {type(e).__name__}: {str(e)[:120]}")
            if st["attempts"][key] >= MAX_ATTEMPTS:
                say(f"**Subtitle check, failed:** {m['title']}: the replace step errored twice. The original is untouched or "
                    f"restored. Needs a look.", False)
                mark(st, row)
            save_state(st)
            continue
        done += 1
        how, backup = method_of(m["title"], started)
        if outcome == "replaced":
            st["replaced"] += 1
            spot = spot_due(st["replaced"])
            if spot:
                st["spot"].append(m["title"])
            say(f"**Subtitle replaced:** {m['title']}\nHow: {how}\nBackup: `{backup}`\nUndo: `subtitle-replace.py --restore \"{m['title']}\"`"
                + ("\n:eyes: **Spot-check this one** when you can, then `subtitle-enforce.py --spot \"" + m["title"] + "\" good|bad`." if spot else ""), False)
        else:
            say(f"**Subtitle unresolved:** {m['title']}: no candidate passed the check. Backup: `{backup}`. "
                f"Tier B keeps the old file; a Tier A fragment was removed and can be restored with `--restore`.", False)
        r.ledger({"title": m["title"], "event": "enforce", "outcome": outcome, "how": how})
        mark(st, row)
        save_state(st)
    if not dry:
        save_state(st)
    log(f"tick done: {done} handled, {len(work)} rows considered")


def status():
    st = load_state()
    print("PAUSED: " + open(PAUSE).read().strip() if os.path.exists(PAUSE) else "not paused")
    if not st:
        return print("no enforce state yet (the first tick sets the go-live time)")
    print(f"live since {datetime.datetime.fromtimestamp(st['since_ts']):%Y-%m-%d %H:%M}; replacements {st['replaced']}; "
          f"handled rows {len(st['handled'])}; spot-checks waiting: {st['spot'] or 'none'}")


def spot(title, verdict):
    if verdict not in ("good", "bad"):
        sys.exit("verdict must be good or bad")
    st = load_state()
    if not st:
        sys.exit("no enforce state yet")
    hits = [t for t in st["spot"] if title.lower() in t.lower()]
    if len(hits) != 1:
        sys.exit(f"{len(hits)} waiting spot-checks match {title!r}: {hits}")
    st["spot"].remove(hits[0]); save_state(st)
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
    ap.add_argument("--spot", nargs=2, metavar=("TITLE", "VERDICT")); ap.add_argument("--resume", action="store_true"); ap.add_argument("--test-post", action="store_true", help="send one clearly-labelled TEST message to Discord")
    a = ap.parse_args()
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
