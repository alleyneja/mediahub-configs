#!/usr/bin/env python3
"""r9 RPCS3 load ladder (issue #52). Waits for a steady game, steps load, records FPS/host/GPU 1 Hz.
Usage: loadtest.py OUTROOT [--dry SECONDS]
Passes: runs once in the current Sunshine state, then re-arms and runs again when the state flips."""
import os, re, subprocess, sys, time, json, signal, statistics as st, urllib.request, urllib.parse

OUT = sys.argv[1]
DRY = int(sys.argv[sys.argv.index("--dry") + 1]) if "--dry" in sys.argv else 0
ENV = dict(os.environ, DISPLAY=":0", XAUTHORITY="/run/user/1000/gdm/Xauthority")
SUNLOG = os.path.expanduser("~/.config/sunshine/sunshine.log")
PLEX = "http://127.0.0.1:32400"
ITEM = "101661"  # Back to the Future, 1080p HEVC 28 Mbps
STEP_S, REC_S = 40, 15
ALLCPU = "0-23"
CCD1 = "6-11,18-23"
children = []
sessions = []

def log(*a):
    print(time.strftime("%T"), *a, flush=True)

def plex_token():
    r = subprocess.run(["docker", "exec", "plex", "sh", "-c",
        'grep -o "PlexOnlineToken=\\"[^\\"]*" "/config/Library/Application Support/Plex Media Server/Preferences.xml"'],
        capture_output=True, text=True)
    return r.stdout.split('"')[1].strip() if '"' in r.stdout else ""
TOKEN = plex_token()

def plex_sessions():
    try:
        rq = urllib.request.Request(PLEX + "/status/sessions", headers={"X-Plex-Token": TOKEN, "Accept": "application/json"})
        md = json.load(urllib.request.urlopen(rq, timeout=5))["MediaContainer"].get("Metadata", [])
        return sum(1 for m in md if m.get("User", {}).get("title") != "alleyneja")
    except Exception:
        return -1

def sunshine_connected():
    try:
        last = None
        for l in open(SUNLOG, errors="replace"):
            if "CLIENT CONNECTED" in l: last = True
            elif "CLIENT DISCONNECTED" in l: last = False
        return bool(last)
    except Exception:
        return False

def title_fps():
    try:
        o = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, env=ENV, timeout=2).stdout
        for l in o.splitlines():
            if ".RPCS3" in l and "FPS:" in l:
                m = re.search(r"FPS:\s*([\d.]+)", l)
                if m: return float(m.group(1))
    except Exception:
        pass
    return None

def cpustat():
    v = list(map(int, open("/proc/stat").readline().split()[1:]))
    return sum(v), v[3] + v[4]

def gpu():
    try:
        o = subprocess.run(["nvidia-smi", "dmon", "-s", "u", "-c", "1"], capture_output=True, text=True, timeout=4).stdout
        row = [l for l in o.splitlines() if l.strip() and not l.startswith("#")][-1].split()
        return [int(x) if x.isdigit() else 0 for x in row[1:5]]  # sm mem enc dec
    except Exception:
        return [0, 0, 0, 0]

def mhz():
    try:
        v = [int(open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq").read()) / 1000 for i in range(0, 24, 3)]
        return sum(v) / len(v)
    except Exception:
        return 0

def game_up():
    return subprocess.run(["pgrep", "-f", "AppRun.wrapped --no-gui"], capture_output=True).returncode == 0

# ---- load generators -------------------------------------------------------
def spawn(cmd):
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
    children.append(p); return p

def stress(args, cpus):
    return [spawn(["taskset", "-c", cpus, "stress-ng", "--timeout", f"{STEP_S + 5}s", "--metrics-brief"] + args)]

def plex_tx(n):
    ps = []
    for i in range(n):
        sid = f"r9lt{int(time.time())}{i}"
        q = urllib.parse.urlencode({"path": f"/library/metadata/{ITEM}", "mediaIndex": 0, "partIndex": 0, "protocol": "http",
            "directPlay": 0, "directStream": 0, "videoQuality": 100, "maxVideoBitrate": 4000, "videoResolution": "1280x720",
            "offset": 600 + 300 * i, "subtitles": "none", "X-Plex-Platform": "Chrome", "X-Plex-Product": "r9loadtest",
            "X-Plex-Client-Identifier": f"r9-loadtest-{sid}", "session": sid, "X-Plex-Token": TOKEN})
        u = f"{PLEX}/video/:/transcode/universal/start.mkv?{q}"
        sessions.append(sid)
        ps.append(spawn(["curl", "-s", "-N", "--max-time", str(STEP_S + 2), "-o", "/dev/null", u]))
    return ps

def stop_all():
    for sid in sessions:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{PLEX}/video/:/transcode/universal/stop?session={sid}", headers={"X-Plex-Token": TOKEN}), timeout=5)
        except Exception:
            pass
    sessions.clear()
    for p in children:
        try: os.killpg(p.pid, signal.SIGTERM)
        except Exception: pass
    time.sleep(1)
    for p in children:
        try: os.killpg(p.pid, signal.SIGKILL)
        except Exception: pass
    children.clear()

CCD0 = "0-5,12-17"
def plex3_verified():
    ps = plex_tx(3)
    time.sleep(15)
    n = subprocess.run("pgrep -fc ^/usr/lib/plexmediaserver/Plex.Transcoder", shell=True, capture_output=True, text=True).stdout.strip()
    e = subprocess.run(["nvidia-smi", "--query-gpu=encoder.stats.sessionCount,encoder.stats.averageFps", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    log("VERIFY plex x3: transcoder procs =", n, "| nvenc sessions/avgFps =", e)
    return ps
def bad_day():
    return stress(["--cpu", "8"], ALLCPU) + plex_tx(2)
STEPS = [
    ("baseline-start", None),
    ("cpu-all-x16", lambda: stress(["--cpu", "16"], ALLCPU)),
    ("cpu-all-x24", lambda: stress(["--cpu", "24"], ALLCPU)),
    ("stream-all-x2", lambda: stress(["--stream", "2"], ALLCPU)),
    ("stream-all-x4", lambda: stress(["--stream", "4"], ALLCPU)),
    ("stream-all-x6", lambda: stress(["--stream", "6"], ALLCPU)),
    ("stream-split-3+3", lambda: stress(["--stream", "3"], CCD0) + stress(["--stream", "3"], CCD1)),
    ("plex-x3-verified", plex3_verified),
    ("bad-day-cpu8+plex2", bad_day),
    ("baseline-end", None),
]

def steady(n=10):
    ok = 0
    while True:
        f = title_fps()
        ok = ok + 1 if (f and f >= 50) else 0
        if ok >= n: return
        time.sleep(1)

def run_pass(idx, sun_state):
    d = f"{OUT}/pass{idx}-{'sunshine' if sun_state else 'local'}"
    os.makedirs(d, exist_ok=True)
    meta = {"kernel": os.uname().release, "start": time.strftime("%F %T"), "sunshine_at_start": sun_state,
            "step_s": STEP_S, "rec_s": REC_S, "plex_item": ITEM}
    json.dump(meta, open(f"{d}/meta.json", "w"))
    csv = open(f"{d}/samples.csv", "w"); csv.write("epoch,step,fps,host_cpu,mhz,gpu_sm,gpu_mem,enc,dec,sunshine,plex_sessions\n")
    prev = cpustat()

    def sample_for(label, secs):
        nonlocal prev
        end = time.time() + secs
        while time.time() < end:
            t = time.time(); c = cpustat()
            busy = 100 * (1 - (c[1] - prev[1]) / max(1, c[0] - prev[0])); prev = c
            g = gpu()
            f = title_fps()
            csv.write(f"{t:.1f},{label},{'' if f is None else f},{busy:.0f},{mhz():.0f},{g[0]},{g[1]},{g[2]},{g[3]},{int(sunshine_connected())},{plex_sessions()}\n")
            csv.flush()
            time.sleep(max(0, 1 - (time.time() - t)))
            if not game_up() and not DRY:
                raise RuntimeError("game exited")

    skipped = []
    try:
        for name, start in STEPS:
            if start is not None and plex_sessions() > 0:
                log("skip", name, "- household Plex stream active"); skipped.append(name); continue
            log("step", name)
            if start is None:
                sample_for(name, 60 if "start" in name else 40); continue
            start()
            sample_for(name, STEP_S)
            stop_all()
            sample_for("recover", REC_S)
    except RuntimeError as e:
        log("aborted:", e)
    finally:
        stop_all(); csv.close()
        json.dump(dict(meta, skipped=skipped, end=time.strftime("%F %T")), open(f"{d}/meta.json", "w"))
    summarize(d)
    return d

def summarize(d):
    rows = [l.strip().split(",") for l in open(f"{d}/samples.csv").read().splitlines()[1:]]
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    out = ["| step | n | fps mean | fps p5 | fps min | <55 | host CPU% | GPU% | enc% | dec% |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in by.items():
        f = sorted(float(r[2]) for r in v if r[2])
        if not f: out.append(f"| {k} | {len(v)} | no FPS |||||||"); continue
        p5 = f[max(0, int(len(f) * 0.05) - 1)]
        m = lambda i: st.mean(float(r[i]) for r in v)
        out.append(f"| {k} | {len(f)} | {st.mean(f):.1f} | {p5:.1f} | {f[0]:.1f} | {sum(x < 55 for x in f)} | {m(3):.0f} | {m(5):.0f} | {m(7):.0f} | {m(8):.0f} |")
    open(f"{d}/summary.md", "w").write("\n".join(out) + "\n")
    log("summary written", d)

if DRY:
    # no game: exercise the generators + sampler for DRY seconds each, short
    os.makedirs(OUT, exist_ok=True)
    STEP_S = REC_S = DRY
    STEPS[:] = [("dry-baseline", None), ("dry-cpu", lambda: stress(["--cpu", "2"], ALLCPU)), ("dry-plex", lambda: plex_tx(1))]
    d = run_pass(0, sunshine_connected())
    print(open(f"{d}/summary.md").read()); sys.exit(0)

signal.signal(signal.SIGTERM, lambda *a: (stop_all(), sys.exit(1)))
steady(5)
log("sunshine connected:", sunshine_connected())
run_pass(3, sunshine_connected())
log("sunshine run done")
