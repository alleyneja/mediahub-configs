#!/usr/bin/env python3
"""ltstress.py - synthetic Live TV viewers through Plex (r9). Token read at runtime, never printed.
phases:  cap   2 viewers on 2 channels for N s, then a 3rd viewer (expects refusal) -> shows what a viewer gets
         churn  K cycles of start viewer / hold / abandon (alternating abandon vs clean stop), health check each cycle
"""
import json, re, subprocess, sys, threading, time, urllib.request, urllib.error, uuid

B = "http://127.0.0.1:32400"
P = subprocess.run("sudo find /srv/docker/plex -name Preferences.xml | head -n1", shell=True, capture_output=True, text=True).stdout.strip()
T = re.search(r'PlexOnlineToken="([^"]*)"', subprocess.run(["sudo", "cat", P], capture_output=True, text=True).stdout).group(1).strip()


def req(path, method="GET", cid="livetv-stress", timeout=20, raw=False):
    r = urllib.request.Request(B + path, method=method, headers={
        "X-Plex-Token": T, "Accept": "application/json", "X-Plex-Client-Identifier": cid, "X-Plex-Product": "stress"})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        b = f.read()
    return b if raw else (json.loads(b) if b else {})


def conns():
    out = subprocess.run("ss -tnH state established '( dport = :80 )'", shell=True, capture_output=True, text=True).stdout.split("\n")
    return sum(1 for l in out if l.strip() and not re.match(r"^(10\.|192\.168\.|127\.)", l.split()[-1]))


def sessions_ok():
    t = time.time()
    try:
        n = req("/livetv/sessions", timeout=10)["MediaContainer"].get("size", 0)
        return "ok(%d) %.1fs" % (n, time.time() - t)
    except Exception as e:
        return "NO ANSWER (%s) after %.1fs" % (str(e)[:40], time.time() - t)


class Viewer(threading.Thread):
    def __init__(self, name, chan, hold):
        super().__init__(daemon=True)
        self.name, self.chan, self.hold = name, chan, hold
        self.cid = "stress-" + uuid.uuid4().hex[:8]
        self.stop = threading.Event()
        self.bytes = 0; self.segs = 0; self.err = ""; self.first = None; self.maxgap = 0; self.started = time.time()

    def run(self):
        try:
            t0 = time.time()
            req("/livetv/dvrs/9/channels/%s/tune" % self.chan, "POST", self.cid, timeout=45)
            key = None
            for _ in range(20):
                for m in req("/livetv/sessions", cid=self.cid)["MediaContainer"].get("Metadata", []):
                    for me in m.get("Media", []):
                        for p in me.get("Part", []):          # Part can be missing for a moment right after tune
                            if "/" + self.cid + "/" in p["key"]:
                                key = p["key"]
                if key: break
                time.sleep(0.5)
            if not key:
                self.err = "no session appeared after tune"; return
            self.first = time.time() - t0
            seen, last = set(), time.time()
            while not self.stop.is_set() and time.time() - self.started < self.hold:
                try:
                    pl = req(key, cid=self.cid, raw=True, timeout=20).decode(errors="ignore")
                except urllib.error.HTTPError as e:
                    if e.code == 404 and time.time() - self.started < 25 and not self.segs:
                        time.sleep(2); continue            # playlist not ready yet right after tune: retry, don't give up
                    raise
                base = key.rsplit("/", 1)[0]
                for l in pl.split("\n"):
                    l = l.strip()
                    if not l or l.startswith("#") or l in seen:
                        continue
                    seen.add(l)
                    u = l if l.startswith("/") else base + "/" + l
                    d = req(u, cid=self.cid, raw=True, timeout=25)
                    self.bytes += len(d); self.segs += 1
                    self.maxgap = max(self.maxgap, time.time() - last); last = time.time()
                time.sleep(1)
        except urllib.error.HTTPError as e:
            self.err = "HTTP %s %s" % (e.code, e.reason)
        except Exception as e:
            self.err = "%s: %s" % (type(e).__name__, str(e)[:60])

    def line(self):
        return "%-8s ch %-5s tune->session %s  segs %-4d MB %-6.1f longest seg gap %.0fs  %s" % (
            self.name, self.chan, ("%.1fs" % self.first) if self.first else "-", self.segs, self.bytes / 1e6, self.maxgap,
            ("ERROR: " + self.err) if self.err else "")


def cleanup(vs):
    for v in vs:
        v.stop.set()
    for v in vs:
        try:
            req("/video/:/transcode/universal/stop?session=" + v.cid, cid=v.cid, timeout=10)
        except Exception:
            pass


def phase_cap(hold):
    print("== cap phase: viewer A (ESPN 1509) + B (WESH 1021), then C (WFTV 1019)")
    a, b = Viewer("A", 1509, 9999), Viewer("B", 1021, 9999)
    a.start(); time.sleep(3); b.start()
    for i in range(hold // 15):
        time.sleep(15)
        print("t+%3ds r9 conns %d  plex %s | A segs %d B segs %d" % (15 * (i + 1), conns(), sessions_ok(), a.segs, b.segs), flush=True)
    c = Viewer("C", 1019, 40)
    c.start(); c.join(60)
    print("C result:", c.line())
    print("after C: r9 conns %d plex %s" % (conns(), sessions_ok()))
    for v in (a, b, c): print(v.line())
    cleanup([a, b, c]); time.sleep(10)
    print("after cleanup: r9 conns %d plex %s" % (conns(), sessions_ok()))


def phase_orphan():
    print("== orphan phase: 2 viewers watch 20 s then VANISH (no stop); at +25 s a 3rd viewer tries; then time the release")
    a, b = Viewer("A", 1509, 20), Viewer("B", 1021, 20)
    a.start(); time.sleep(2); b.start(); a.join(40); b.join(40)
    t0 = time.time()
    print("viewers gone: A segs %d err %s | B segs %d err %s | conns %d plex %s" % (a.segs, a.err or "-", b.segs, b.err or "-", conns(), sessions_ok()), flush=True)
    time.sleep(5)
    c = Viewer("C", 1019, 30)
    c.start(); c.join(50)
    print("3rd viewer %.0fs after A/B vanished:" % (time.time() - t0 - 0), c.line())
    freed = None
    for i in range(120):
        n = req("/livetv/sessions", timeout=10)["MediaContainer"].get("size", 0)
        if n == 0 and conns() == 0:
            freed = time.time() - t0; break
        time.sleep(5)
    print("all sessions + provider connections released %s after the viewers vanished" % (("%.0f s" % freed) if freed else "NOT within 10 min"))
    print("after: conns %d plex %s" % (conns(), sessions_ok()))
    cleanup([a, b, c])


def phase_hold(hold, post):
    """Fill both slots with 2 synthetic viewers for `hold` s so a REAL client can try a 3rd channel,
    then drop them abruptly (no stop call) and keep logging for `post` s while the real client retries."""
    a, b = Viewer("A", 1509, 99999), Viewer("B", 1021, 99999)
    a.start(); time.sleep(12); b.start()
    for _ in range(25):
        if a.segs and b.segs: break
        time.sleep(2)
    t_drop = time.time() + hold
    print("%s READY: A(ESPN 1509) + B(WESH 1021) streaming, conns %d, segs %d/%d. Real client: try a 3rd channel until %s" % (
        time.strftime("%H:%M:%S"), conns(), a.segs, b.segs, time.strftime("%H:%M:%S", time.localtime(t_drop))), flush=True)
    while time.time() < t_drop:
        time.sleep(10)
        print("%s hold  conns %d plex %s segs A %d%s B %d%s" % (time.strftime("%H:%M:%S"), conns(), sessions_ok(), a.segs,
              (" [A ended: %s]" % (a.err or "no error")) if not a.is_alive() else "", b.segs,
              (" [B ended: %s]" % (b.err or "no error")) if not b.is_alive() else ""), flush=True)
    a.stop.set(); b.stop.set()
    print("%s DROP: viewers vanished abruptly (no stop). Real client: retry now, within 2 minutes" % time.strftime("%H:%M:%S"), flush=True)
    t0 = time.time()
    while time.time() - t0 < post:
        time.sleep(5)
        print("%s after-drop +%3ds conns %d plex %s" % (time.strftime("%H:%M:%S"), time.time() - t0, conns(), sessions_ok()), flush=True)
    print("%s end. conns %d plex %s" % (time.strftime("%H:%M:%S"), conns(), sessions_ok()), flush=True)


def phase_churn(cycles):
    chans = [1509, 1021, 1018, 1020, 1019]
    print("== churn phase: %d cycles, 2 viewers at a time, alternating abandon / clean stop" % cycles)
    for i in range(cycles):
        vs = [Viewer("c%da" % i, chans[i % 5], 9999), Viewer("c%db" % i, chans[(i + 2) % 5], 9999)]
        for v in vs: v.start(); time.sleep(1.5)
        time.sleep(12)
        errs = [v.err for v in vs if v.err]
        mode = "abandon" if i % 2 == 0 else "clean-stop"
        for v in vs: v.stop.set()
        if mode == "clean-stop":
            cleanup(vs)
        time.sleep(3)
        print("cycle %2d %-10s conns %d plex %s segs %s %s" % (i, mode, conns(), sessions_ok(), [v.segs for v in vs], ("ERR " + "; ".join(errs)) if errs else ""), flush=True)


if __name__ == "__main__":
    ph = sys.argv[1]
    try:
        {"cap": lambda: phase_cap(int(sys.argv[2])), "orphan": phase_orphan, "hold": lambda: phase_hold(int(sys.argv[2]), int(sys.argv[3])), "churn": lambda: phase_churn(int(sys.argv[2]))}[ph]()
    except KeyboardInterrupt:
        pass
    print("final: plex", sessions_ok(), "conns", conns())
