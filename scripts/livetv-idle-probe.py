#!/usr/bin/env python3
"""livetv-idle-probe.py - keeps ONE Live TV stream open while nobody is watching (#87).

Companion to livetv-session-tap.py (r9), which logs real viewing sessions. Together:
  tap   = covers every second someone IS watching (passive, no provider connection)
  probe = covers the time nobody is (this script, one provider connection)

The provider allows 2 simultaneous connections. This holds at most 1, and only when idle:
  * starts only after the account has shown 0 active connections for QUIET_SECS in a row
  * the moment active_cons >= 2 (ours + someone else) it closes and goes back to waiting
  * note the provider keeps counting a closed connection for ~5 s, so a SECOND viewer who
    starts within ~5 s of the first can still get a "Playback error" (known cost)
STOP file = kill switch (`touch ~/logs/livetv-idle-probe/STOP`; the service then exits).

Writes ~/logs/livetv-idle-probe/probe.csv, one row per second while connected:
  ts,recv_kBps,note        note: '' | connect | drop:<short reason>
Stream URL and credentials are read at runtime from Threadfin's playlist and never logged.

Usage:  livetv-idle-probe.py            run forever (systemd, host that has the Threadfin playlist)
        livetv-idle-probe.py --summary  stalls, longest stall, coverage
"""
import csv, json, os, re, select, sys, threading, time, urllib.request
from datetime import datetime

DIR = os.environ.get("PROBE_LOGDIR", os.path.expanduser("~/logs/livetv-idle-probe"))
CSV = os.path.join(DIR, "probe.csv")
STOP = os.path.join(DIR, "STOP")
M3U = "/srv/docker/threadfin/conf/data/MLXG7IZI17KCJ5IPBDCN.m3u"
TVG_ID = "nbcwesh.us"            # same channel as the sampler (ch 1021 WESH)
QUIET_SECS = int(os.environ.get("PROBE_QUIET_SECS", "300"))
SESSION_MAX = 3600               # reconnect hourly so one long session cannot go stale
MAX_BYTES = 100 * 1024 * 1024


def creds():
    t = open(M3U, errors="ignore").read().split("\n")
    for i, l in enumerate(t):
        if l.startswith("#EXTINF") and 'tvg-id="%s"' % TVG_ID in l:
            url = t[i + 1].strip()
            m = re.match(r"(https?://[^/]+)/([^/]+)/([^/]+)/(\d+)\.(ts|m3u8)$", url)
            if m:
                return url, m.group(1), m.group(2), m.group(3)
    raise RuntimeError("channel not in playlist")


def active_cons(host, user, pw):
    u = "%s/player_api.php?username=%s&password=%s" % (host, user, pw)
    return int(json.load(urllib.request.urlopen(u, timeout=10))["user_info"]["active_cons"])


def log(rows):
    if not rows:
        return
    os.makedirs(DIR, exist_ok=True)
    if os.path.exists(CSV) and os.path.getsize(CSV) > MAX_BYTES:
        os.replace(CSV, CSV + ".1")
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "recv_kBps", "note"])
        w.writerows(rows)


def stamp(sec):
    return datetime.fromtimestamp(sec).isoformat(timespec="seconds")


def probe_session(url, host, user, pw):
    """Hold the stream until a viewer appears, an error, or SESSION_MAX. Returns a reason string."""
    viewer = threading.Event()

    def watch():                       # poll the provider; ours counts as 1
        while not viewer.is_set() and not os.path.exists(STOP):
            time.sleep(2)
            try:
                if active_cons(host, user, pw) >= 2:
                    viewer.set()
            except Exception:
                pass                   # a failed poll must not drop the probe
    threading.Thread(target=watch, daemon=True).start()

    try:
        r = urllib.request.urlopen(url, timeout=15)
        sock = r.fp.raw._sock
    except Exception as e:
        viewer.set()
        log([[stamp(int(time.time())), 0, "drop:connect " + str(e)[:40]]])
        return "connect-failed"
    log([[stamp(int(time.time())), 0, "connect"]])

    begin = time.time()
    cur, nbytes, rows = int(begin), 0, []
    reason = "session-max"
    while True:
        if os.path.exists(STOP):
            reason = "stop"; break
        if viewer.is_set():
            reason = "viewer"; break
        if time.time() - begin > SESSION_MAX:
            break
        try:
            # wait up to 1 s for data (so silent seconds are still counted), never time out the reader itself
            if select.select([sock], [], [], 1.0)[0]:
                d = r.read1(65536)
                if not d:
                    reason = "stream-ended"; break
                nbytes += len(d)
        except Exception as e:
            reason = "error " + str(e)[:30]; break
        now = int(time.time())
        while cur < now:               # flush finished seconds, silent seconds are written as 0
            rows.append([stamp(cur), int(nbytes / 1024), ""]); nbytes = 0; cur += 1
        if rows:
            log(rows); rows = []
    viewer.set()
    try:
        r.close()
    except Exception:
        pass
    log([[stamp(int(time.time())), 0, "drop:" + reason]])
    return reason


def run():
    os.makedirs(DIR, exist_ok=True)
    quiet_since = None
    while not os.path.exists(STOP):
        try:
            url, host, user, pw = creds()
            n = active_cons(host, user, pw)
        except Exception:
            time.sleep(30); continue
        if n == 0:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since >= QUIET_SECS:
                reason = probe_session(url, host, user, pw)
                quiet_since = None
                # after a viewer-yield, wait QUIET_SECS of 0 again; after an error retry soon
                time.sleep(5 if reason in ("session-max",) else 15)
                continue
        else:
            quiet_since = None
        time.sleep(5)


def judge(k, win=10, need=1000, quiet=50):
    """k = kB received per second. Bursty feed: judge a rolling `win`-second average against `need`.
    -> (starved_seconds, longest_starved_run, longest_silent_gap_seconds)"""
    starved = []
    for i in range(len(k)):
        w = k[max(0, i - win + 1):i + 1]
        starved.append(len(w) == win and sum(w) / win < need)
    def longest(flags):
        best = cur = 0
        for f in flags:
            cur = cur + 1 if f else 0
            best = max(best, cur)
        return best
    return sum(starved), longest(starved), longest([x < quiet for x in k])


def summary():
    if not os.path.exists(CSV):
        print("no data yet"); return
    rows = list(csv.DictReader(open(CSV)))
    data = [r for r in rows if not r["note"]]
    k = [int(r["recv_kBps"]) for r in data]
    if not k:
        print("no samples"); return
    starved, longest, gap = judge(k)
    drops = [r for r in rows if r["note"].startswith("drop:") and "viewer" not in r["note"] and "stop" not in r["note"] and "session-max" not in r["note"]]
    ks = sorted(k)
    print("seconds measured: %d (%.1f h)   %s -> %s" % (len(k), len(k) / 3600.0, data[0]["ts"], data[-1]["ts"]))
    print("starved seconds (10 s average < 1000 kB/s): %d   longest starved run: %d s   longest silent gap: %d s   mean: %d kB/s" % (starved, longest, gap, sum(k) // len(k)))
    print("viewer yields: %d   unexpected drops: %d" % (sum(1 for r in rows if r["note"] == "drop:viewer"), len(drops)))
    for r in drops[-5:]:
        print("   ", r["ts"], r["note"])


if __name__ == "__main__":
    summary() if "--summary" in sys.argv else run()
