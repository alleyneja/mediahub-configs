#!/usr/bin/env python3
"""Synthetic headless Plex transcode load test for r9 (issue #43 / #48).

Starts N real Plex HLS transcode sessions through the Plex API (hardware Plex Transcoder, same code
path as a viewer), pulls segments at playback pace, and logs GPU stats every second.
Aborts instantly and stops every test session if a REAL viewer, Sunshine client or emulator appears.
Stdlib only. The Plex token is read from Preferences.xml and never printed.
"""
import argparse, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid, csv, signal

PLEX = "http://127.0.0.1:32400"
PREFS = "/srv/docker/plex/Library/Application Support/Plex Media Server/Preferences.xml"
TOKEN = re.search(r'PlexOnlineToken="([^"]+)"', open(PREFS).read()).group(1)
RUN_ID = "synthload"
PROFILES = {  # what the client asks for
    "remote5": {"maxVideoBitrate": "5000", "videoResolution": "1920x1080"},   # the new WAN cap
    "orig20":  {"maxVideoBitrate": "20000", "videoResolution": "1920x1080"},  # heavy LAN transcode
}
abort = threading.Event()
abort_reason = []


def req(path, params=None, accept_json=True, timeout=20):
    url = PLEX + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    r = urllib.request.Request(url, headers={"X-Plex-Token": TOKEN, **({"Accept": "application/json"} if accept_json else {})})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return f.read()


def req_retry(path, params=None, accept_json=False, timeout=60, tries=40, delay=1.0):
    """Plex 404s a playlist/segment until the transcoder has produced it."""
    last = None
    for _ in range(tries):
        try:
            return req(path, params, accept_json, timeout)
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 404:
                raise
            time.sleep(delay)
    raise last


def sessions():
    d = json.loads(req("/status/sessions"))["MediaContainer"]
    return d.get("Metadata", [])


def ours(m):
    return RUN_ID in (m.get("Player", {}).get("machineIdentifier", "") or "") or RUN_ID in (m.get("Session", {}).get("id", "") or "")


def foreign_activity():
    """Return a reason string if anything that is not this test is using the box, else ''."""
    try:
        for m in sessions():
            if not ours(m):
                return "foreign plex session: %s on %s" % (m.get("title"), m.get("Player", {}).get("product"))
    except Exception as e:
        return "plex unreadable: %s" % e
    out = subprocess.run("ss -tnH state established '( sport = :48010 or sport = :47984 or sport = :47989 )' | wc -l",
                         shell=True, capture_output=True, text=True).stdout.strip()
    if out and out != "0":
        return "sunshine connection(s): %s" % out
    p = subprocess.run(r"pgrep -fa 'Ryujinx|rpcs3|dolphin-emu|pcsx2|retroarch|moonlight|steam ' | grep -v pgrep | grep -v plexload",
                       shell=True, capture_output=True, text=True).stdout.strip()
    if p:
        return "emulator/game process: %s" % p.splitlines()[0][:80]
    return ""


class Stream(threading.Thread):
    def __init__(self, idx, rk, profile, duration_hint):
        super().__init__(daemon=True)
        self.idx, self.rk, self.profile = idx, rk, profile
        self.cid = "%s-%d-%s" % (RUN_ID, idx, uuid.uuid4().hex[:6])
        self.session = self.cid
        self.stop_flag = threading.Event()
        self.segments = 0
        self.err = ""

    def params(self):
        p = {"path": "/library/metadata/%s" % self.rk, "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
             "offset": "0", "directPlay": "0", "directStream": "0",
             "subtitles": "none", "audioBoost": "100", "location": "lan", "hasMDE": "1",
             "session": self.session, "X-Plex-Session-Identifier": self.session,
             "X-Plex-Client-Identifier": self.cid, "X-Plex-Product": "Plex Web", "X-Plex-Platform": "Chrome",
             "X-Plex-Device": "Linux", "X-Plex-Device-Name": "synthload-%d" % self.idx, "X-Plex-Version": "4.0"}
        p.update(PROFILES[self.profile])
        return p

    def run(self):
        """Behave like a real player: prebuffer 2 segments, play at 1x, fetch at most ~12 s ahead.
        A segment that arrives after its playback deadline is a stall."""
        self.startup = None; self.late = []
        try:
            req("/video/:/transcode/universal/decision", self.params(), accept_json=False)
            master = req("/video/:/transcode/universal/start.m3u8", self.params(), accept_json=False).decode()
            sub = [l.strip() for l in master.splitlines() if l.strip() and not l.startswith("#")][0]
            base = "/video/:/transcode/universal/"
            t_req = time.time()
            pl = req_retry(base + sub, {"X-Plex-Session-Identifier": self.session, "X-Plex-Client-Identifier": self.cid}).decode()
            segs = [l.strip() for l in pl.splitlines() if l.strip() and not l.startswith("#")]
            d = os.path.dirname(sub)
            play0 = None
            for k, sname in enumerate(segs):
                if self.stop_flag.is_set() or abort.is_set():
                    break
                if play0 is not None:                      # don't run more than 12 s ahead of the playhead
                    wait = play0 + 3 * k - 12 - time.time()
                    while wait > 0 and not (self.stop_flag.is_set() or abort.is_set()):
                        time.sleep(min(wait, 0.5)); wait = play0 + 3 * k - 12 - time.time()
                req(base + d + "/" + sname, accept_json=False, timeout=60)
                now = time.time(); self.segments += 1
                if k == 0:
                    self.startup = round(now - t_req, 2)
                if k == 1:
                    play0 = now                            # playback begins once 2 segments are buffered
                if play0 is not None and k >= 1:
                    self.late.append(round(max(0.0, now - (play0 + 3 * (k - 1))), 2))
        except Exception as e:
            self.err = str(e)[:120]

    def stop(self):
        self.stop_flag.set()
        try:
            req("/video/:/transcode/universal/stop", {"session": self.session, "X-Plex-Client-Identifier": self.cid}, accept_json=False, timeout=10)
        except Exception:
            pass


def cleanup_orphans():
    """Stop any synthload transcode sessions still alive (e.g. after a killed run)."""
    out = subprocess.run("docker exec plex sh -c 'ps -eo args | grep [T]ranscoder'", shell=True, capture_output=True, text=True).stdout
    for sid in set(re.findall(r"synthload-\d+-[0-9a-f]+", out)):
        try:
            req("/video/:/transcode/universal/stop", {"session": sid}, accept_json=False, timeout=10)
        except Exception:
            pass


def start_gpu_logger(path):
    fields = ("timestamp,memory.used,utilization.gpu,utilization.memory,utilization.encoder,utilization.decoder,"
              "power.draw,temperature.gpu,clocks.sm,encoder.stats.sessionCount,encoder.stats.averageFps,"
              "encoder.stats.averageLatency,clocks_throttle_reasons.active")
    f = open(path, "w")
    return subprocess.Popen(["nvidia-smi", "--query-gpu=" + fields, "--format=csv", "-lms", "1000"], stdout=f, stderr=subprocess.DEVNULL)


def procs_mem():
    out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    tot = {"plex": 0, "other": 0, "n_plex": 0}
    rows = []
    for l in out.strip().splitlines():
        pid, name, mem = [x.strip() for x in l.split(",")]
        rows.append((name.split("/")[-1], int(mem)))
        if "Transcoder" in name:
            tot["plex"] += int(mem); tot["n_plex"] += 1
        else:
            tot["other"] += int(mem)
    return tot


def phase(n, secs, profile, items, out):
    streams = [Stream(i, items[i % len(items)], profile, secs) for i in range(n)]
    t0 = time.time()
    for s in streams:
        s.start(); time.sleep(2)
    samples = []
    end = time.time() + secs
    while time.time() < end and not abort.is_set():
        reason = foreign_activity()
        if reason:
            abort_reason.append(reason); abort.set(); break
        row = {"t": round(time.time() - t0), "plex_sessions": 0, "speeds": [], "throttled": 0, "hw": 0}
        try:
            for m in sessions():
                if ours(m):
                    row["plex_sessions"] += 1
                    ts = m.get("TranscodeSession", {}) or {}
                    if ts.get("speed") is not None: row["speeds"].append(float(ts["speed"]))
                    row["throttled"] += 1 if ts.get("throttled") else 0
                    row["hw"] += 1 if ts.get("transcodeHwFullPipeline") or ts.get("transcodeHwEncoding") else 0
        except Exception as e:
            row["err"] = str(e)[:60]
        row.update(procs_mem())
        samples.append(row)
        time.sleep(4)
    for s in streams: s.stop()
    for s in streams: s.join(timeout=8)
    res = {"n": n, "profile": profile, "secs": round(time.time() - t0), "aborted": abort.is_set(),
           "stream_errors": [s.err for s in streams if s.err], "segments": [s.segments for s in streams],
           "startup_s": [s.startup for s in streams], "max_late_s": [max(s.late) if s.late else None for s in streams],
           "stalls": [sum(1 for x in s.late if x > 0.5) for s in streams],
           "samples": samples}
    out.write(json.dumps(res) + "\n"); out.flush()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ramp", default="1", help="comma list of concurrent stream counts, run in order")
    ap.add_argument("--secs", type=int, default=60)
    ap.add_argument("--profile", default="remote5", choices=PROFILES)
    ap.add_argument("--items", default="11135", help="ratingKeys to rotate through")
    ap.add_argument("--outdir", default=os.path.expanduser("~/loadtest/results"))
    ap.add_argument("--cooldown", type=int, default=20)
    a = ap.parse_args()
    items = a.items.split(",")
    rd = os.path.join(a.outdir, time.strftime("%Y%m%d-%H%M%S")); os.makedirs(rd, exist_ok=True)
    cleanup_orphans(); time.sleep(3)
    reason = foreign_activity()
    if reason:
        print("REFUSING TO START:", reason); sys.exit(2)
    gl = start_gpu_logger(os.path.join(rd, "gpu.csv"))
    out = open(os.path.join(rd, "phases.jsonl"), "w")
    signal.signal(signal.SIGTERM, lambda *_: abort.set())
    print("results dir:", rd, flush=True)
    time.sleep(5)  # idle baseline lines in gpu.csv
    try:
        for n in [int(x) for x in a.ramp.split(",")]:
            if abort.is_set(): break
            print("phase: %d streams, %s, %ds" % (n, a.profile, a.secs), flush=True)
            r = phase(n, a.secs, a.profile, items, out)
            print("  done: startup %s s, stalls %s, max lateness %s s, segments %s, errors %d, aborted %s" % (
                r["startup_s"], r["stalls"], r["max_late_s"], r["segments"], len(r["stream_errors"]), r["aborted"]), flush=True)
            time.sleep(a.cooldown)
    finally:
        gl.terminate(); cleanup_orphans()
        if abort_reason: print("ABORTED:", abort_reason[0])
        print("end")


if __name__ == "__main__":
    main()
