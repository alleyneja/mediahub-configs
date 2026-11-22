#!/usr/bin/env python3
"""livetv-viewer-log.py - passive record of WHO is watching and on WHAT device (#87).

Run on r9. Polls Plex's own /status/sessions (local API) every POLL seconds and writes one row per
active session. Opens no provider connection and uses no provider slot. Plex keeps no history of
Live TV viewers and its detailed logs rotate in under an hour, so without this a "Playback error"
report cannot be tied to an account, device or channel afterwards.

Log: ~/logs/livetv-viewer-log/viewers.csv
  ts,session,live,user,device,product,platform,address,remote,state,title,channel,decision,transcode_kbps,
  bandwidth_kbps (Plex's per-session delivery estimate), tx_speed (x realtime; <1.0 = server too slow), throttled, hw, src_codec
  Also load.csv every poll: ts,cpu_pct,gpu_pct,gpu_enc_pct,gpu_dec_pct,net_tx_kbps,net_rx_kbps,transcoders (r9 totals).
  (also rows with session='-' and user='API-NO-ANSWER' when Plex stops answering, with the error)
The Plex token is read from Preferences.xml at runtime and never logged. Rotates at 50 MB.
Env: VIEWER_LOGDIR, VIEWER_POLL (seconds, default 10).
Usage: livetv-viewer-log.py            run forever (systemd)
       livetv-viewer-log.py --summary  who watched what, per session, from the log
"""
import collections, csv, glob, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta

LOGDIR = os.environ.get("VIEWER_LOGDIR", os.path.expanduser("~/logs/livetv-viewer-log"))
CSV = os.path.join(LOGDIR, "viewers.csv")
POLL = int(os.environ.get("VIEWER_POLL", "10"))
BASE = "http://127.0.0.1:32400"
COLS = ["ts", "session", "live", "user", "device", "product", "platform", "address", "remote", "state",
        "title", "channel", "decision", "transcode_kbps", "bandwidth_kbps", "tx_speed", "throttled", "hw", "src_codec"]
LOAD_CSV = os.path.join(LOGDIR, "load.csv")
LOAD_COLS = ["ts", "cpu_pct", "gpu_pct", "gpu_enc_pct", "gpu_dec_pct", "net_tx_kbps", "net_rx_kbps", "transcoders"]


def token():
    for p in glob.glob("/srv/docker/plex/**/Preferences.xml", recursive=True):
        m = re.search(r'PlexOnlineToken="([^"]*)"', open(p, errors="ignore").read())
        if m:
            return m.group(1).strip()
    raise RuntimeError("no Plex token found")


def fetch(tok):
    r = urllib.request.Request(BASE + "/status/sessions", headers={"X-Plex-Token": tok, "Accept": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=8))["MediaContainer"].get("Metadata", [])


def row(m, now):
    p, u, s = m.get("Player", {}), m.get("User", {}), m.get("Session", {})
    t = m.get("TranscodeSession", {})
    media = (m.get("Media") or [{}])[0]
    dec = "%s/%s" % (t.get("videoDecision", "-"), t.get("audioDecision", "-")) if t else "direct"
    chan = m.get("grandparentTitle") or media.get("channelTitle") or media.get("channelCallSign") or ""
    return [now, (s.get("id") or m.get("sessionKey") or "?")[:8], "1" if m.get("live") else "0", u.get("title", "?"),
            p.get("title", "?"), p.get("product", "?"), p.get("platform", "?"),
            p.get("remotePublicAddress") or p.get("address", "?"), "0" if p.get("local") else "1", p.get("state", "?"),
            (m.get("title") or "")[:40], chan[:30], dec, int(t.get("bitrate") or 0) if t else 0,
            int(s.get("bandwidth") or 0),                         # Plex's own estimate of this session's delivery rate (kbps)
            t.get("speed", "") if t else "",                       # transcode speed, x realtime: < 1.0 means the SERVER can't keep up
            int(bool(t.get("throttled"))) if t else "",
            ("full" if t.get("transcodeHwFullPipeline") else "enc" if t.get("transcodeHwEncoding") else "cpu") if t else "",
            t.get("sourceVideoCodec", "") if t else ""]


def pad(r):
    return r + [""] * (len(COLS) - len(r))


def write(rows):
    if not rows:
        return
    os.makedirs(LOGDIR, exist_ok=True)
    if os.path.exists(CSV) and os.path.getsize(CSV) > 50 * 1024 * 1024:
        os.replace(CSV, CSV + ".1")
    if os.path.exists(CSV):                                   # columns changed since the file was started: keep the old file aside
        with open(CSV) as f:
            if f.readline().strip().split(",") != COLS:
                os.replace(CSV, CSV + ".pre-cols-" + datetime.now().strftime("%Y%m%d%H%M"))
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        w.writerows(rows)


class Load:
    """r9 CPU %, GPU %, NIC tx/rx kbps (default-route interface), Plex Transcoder count. Passive reads only."""
    def __init__(self):
        self.iface = self._iface(); self.prev_cpu = self._cpu(); self.prev_net = self._net(); self.prev_t = time.time()

    @staticmethod
    def _iface():
        try:
            out = subprocess.run(["ip", "-o", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=3).stdout
            return re.search(r"dev (\S+)", out).group(1)
        except Exception:
            return None

    @staticmethod
    def _cpu():
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(v), v[3] + v[4]                               # total, idle+iowait

    def _net(self):
        for l in open("/proc/net/dev"):
            if self.iface and l.strip().startswith(self.iface + ":"):
                f = l.split(":")[1].split()
                return int(f[0]), int(f[8])                      # rx bytes, tx bytes
        return 0, 0

    def sample(self, now):
        t = time.time(); dt = max(t - self.prev_t, 0.001)
        cpu = self._cpu(); dtot = cpu[0] - self.prev_cpu[0]
        pct = 100.0 * (1 - (cpu[1] - self.prev_cpu[1]) / dtot) if dtot else 0
        net = self._net(); rx = (net[0] - self.prev_net[0]) * 8 / 1000 / dt; tx = (net[1] - self.prev_net[1]) * 8 / 1000 / dt
        self.prev_cpu, self.prev_net, self.prev_t = cpu, net, t
        gpu = ["", "", ""]
        try:
            o = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=4).stdout.strip().split(",")
            gpu = [x.strip() for x in o][:3]
        except Exception:
            pass
        try:
            n = subprocess.run(["pgrep", "-fc", "Plex Transcoder"], capture_output=True, text=True, timeout=3).stdout.strip() or "0"
        except Exception:
            n = ""
        return [now, "%.0f" % pct] + gpu + ["%.0f" % tx, "%.0f" % rx, n]


def write_load(row):
    os.makedirs(LOGDIR, exist_ok=True)
    if os.path.exists(LOAD_CSV) and os.path.getsize(LOAD_CSV) > 50 * 1024 * 1024:
        os.replace(LOAD_CSV, LOAD_CSV + ".1")
    new = not os.path.exists(LOAD_CSV)
    with open(LOAD_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(LOAD_COLS)
        w.writerow(row)


# ---- incident notice: a live session stuck in "buffering" -> snapshot + one Discord line with a verdict ----
INCIDENT_DIR = os.environ.get("VIEWER_INCIDENTS", os.path.expanduser("~/logs/livetv-incidents"))
WEBHOOK_ENV = os.environ.get("VIEWER_WEBHOOK_ENV", os.path.expanduser("~/mediahub-configs/scripts/fleet-healthcheck.env"))   # FLEET_HEALTH_WEBHOOK=...
TAP_CSV = os.path.expanduser("~/logs/livetv-session-tap/tap.csv")
PLEX_LOG = "/srv/docker/plex/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log"
STALL_SAMPLES = int(os.environ.get("VIEWER_STALL_SAMPLES", "3"))      # consecutive buffering samples (10 s each) before we call it a stall
COOLDOWN = int(os.environ.get("VIEWER_COOLDOWN", "900"))              # one notice per device per 15 min
SECRET = re.compile(r"(X-Plex-Token|token|password|username)=[^&\s\"']+", re.I)
URLS = re.compile(r"https?://\S+")


def redact(line):
    return URLS.sub("<url>", SECRET.sub(r"\1=***", line))


def tail_lines(path, nbytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - nbytes))
            return f.read().decode(errors="ignore").splitlines()
    except Exception:
        return []


def feed_stats(now_dt):
    """Streams the stream-logger saw in the last 10 min -> [(mean kB/s over last 90 s, longest silent gap s)]. Ignores idle connections (e.g. tailscaled)."""
    cut90 = (now_dt - timedelta(seconds=90)).isoformat(timespec="seconds")
    cut600 = (now_dt - timedelta(seconds=600)).isoformat(timespec="seconds")
    by = collections.defaultdict(list)
    for l in tail_lines(TAP_CSV, 400000):
        p = l.split(",")
        if len(p) >= 3 and p[0] >= cut600:
            try:
                by[p[1]].append((p[0], float(p[2])))
            except ValueError:
                pass
    out = []
    for c, rs in by.items():
        if sum(k for _, k in rs) < 3000:                    # < ~3 MB in 10 min: not a stream
            continue
        recent = [k for t, k in rs if t >= cut90]
        if len(recent) < 15:
            continue
        gap = run = 0
        for k in recent:
            run = run + 1 if k < 50 else 0
            gap = max(gap, run)
        out.append((sum(recent) / len(recent), gap))
    return out


def snapshot(dev, now_dt):
    name = now_dt.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"\W+", "_", dev)[:20]
    d = os.path.join(INCIDENT_DIR, name)
    os.makedirs(d, exist_ok=True)
    cutoff = now_dt - timedelta(minutes=12)
    keep, ok = [], False
    for l in tail_lines(PLEX_LOG, 12 * 1024 * 1024):          # Plex logs rotate in ~45 min: copy the last 12 min now, credentials removed
        try:
            ok = datetime.strptime(l[:20], "%b %d, %Y %H:%M:%S") >= cutoff
        except ValueError:
            pass                                              # continuation line: keep the previous decision
        if ok:
            keep.append(redact(l))
    with open(os.path.join(d, "plex-log-last12min.txt"), "w") as f:
        f.write("\n".join(keep[-60000:]))
    cut4 = (now_dt - timedelta(minutes=4)).isoformat(timespec="seconds")
    for src, dst, n in ((CSV, "viewers-tail.csv", 200000), (LOAD_CSV, "load-tail.csv", 60000), (TAP_CSV, "stream-tail.csv", 400000)):
        with open(os.path.join(d, dst), "w") as f:
            f.write("\n".join(l for l in tail_lines(src, n) if l[:4] != "2026" or l[:19] >= cut4))
    dirs = sorted(x for x in os.listdir(INCIDENT_DIR) if os.path.isdir(os.path.join(INCIDENT_DIR, x)))
    for old in dirs[:-40]:
        shutil.rmtree(os.path.join(INCIDENT_DIR, old), ignore_errors=True)
    return d


def verdict(streams, load_row):
    try:
        cpu, gpu, enc = float(load_row[1] or 0), float(load_row[2] or 0), float(load_row[3] or 0)
    except (ValueError, IndexError):
        cpu = gpu = enc = 0
    if not streams:
        return "no active provider stream was visible to the logger (stream may have dropped)"
    if any(m < 300 or g >= 20 for m, g in streams):          # 10-15 s gaps are normal for this bursty feed (~20/hour, hidden by Plex's buffer)
        return "the FEED into r9 stalled (provider side or the route to the provider)"
    if cpu >= 60 or gpu >= 90 or enc >= 90:
        return "r9 was BUSY (CPU/GPU high)"
    return "feed and r9 both look fine, so the problem is between r9 and the viewer (internet or their Wi-Fi/device)"


def build_message(m, now_dt, load_row, streams, folder, secs):
    p, s, t = m.get("Player", {}), m.get("Session", {}), m.get("TranscodeSession", {}) or {}
    feeds = ", ".join("%.0f kB/s%s" % (mean, "" if gap < 20 else " (%d s gap)" % gap) for mean, gap in streams) or "none seen"
    return ("Live TV stall: %s (%s) on \"%s\", buffering for %d s+ at %s.\n"
            "Feed into r9: %s. r9: CPU %s%%, GPU %s%%. Transcode %s x real time.\n"
            "Verdict: %s.\nSnapshot on r9: %s" % (
                p.get("title", "?"), "remote" if not p.get("local") else "home network", (m.get("title") or "?")[:40], secs,
                now_dt.strftime("%H:%M:%S"), feeds, load_row[1] if len(load_row) > 1 else "?", load_row[2] if len(load_row) > 2 and load_row[2] != "" else "?",
                ("%.1f" % float(t["speed"])) if t.get("speed") not in (None, "") else "?", verdict(streams, load_row), folder.replace(os.path.expanduser("~"), "~")))


def post_discord(msg):
    try:
        hook = next(l.split("=", 1)[1].strip().strip("\"'") for l in open(WEBHOOK_ENV) if l.startswith("FLEET_HEALTH_WEBHOOK="))
        req = urllib.request.Request(hook, data=json.dumps({"content": msg[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "livetv-viewer-log/1.0"})
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception:
        return False


class Incidents:
    def __init__(self):
        self.streak, self.last = {}, {}

    def check(self, meta, now_dt, load_row):
        seen = set()
        for m in meta:
            if not m.get("live"):
                continue
            k = m.get("Player", {}).get("title", "?")
            seen.add(k)
            self.streak[k] = self.streak.get(k, 0) + 1 if m.get("Player", {}).get("state") == "buffering" else 0
            if self.streak[k] >= STALL_SAMPLES and now_dt.timestamp() - self.last.get(k, 0) > COOLDOWN:
                self.last[k] = now_dt.timestamp()
                try:
                    folder = snapshot(k, now_dt)
                    msg = build_message(m, now_dt, load_row, feed_stats(now_dt), folder, self.streak[k] * POLL)
                    with open(os.path.join(INCIDENT_DIR, "incidents.log"), "a") as f:
                        f.write(now_dt.isoformat(timespec="seconds") + " " + msg.replace("\n", " | ") + "\n")
                    post_discord(msg)
                except Exception:
                    pass                                       # an incident notice must never stop the viewer log
        for k in list(self.streak):
            if k not in seen:
                self.streak.pop(k)


def selftest():
    """Build (and save a snapshot for) a notice from the current state without posting."""
    now = datetime.now()
    meta = fetch(token())
    m = next((x for x in meta if x.get("live")), meta[0] if meta else {"Player": {"title": "(no session)", "local": True}, "title": "(none)"})
    ld = Load(); time.sleep(2); row_ = ld.sample(now.isoformat(timespec="seconds"))
    d = snapshot("selftest", now)
    print(build_message(m, now, row_, feed_stats(now), d, 30))
    print("snapshot files:", ", ".join("%s %dKB" % (f, os.path.getsize(os.path.join(d, f)) // 1024) for f in sorted(os.listdir(d))))
    shutil.rmtree(d, ignore_errors=True)


def run():
    tok, down = None, False
    load = Load()
    inc = Incidents()
    while True:
        now_dt = datetime.now()
        now = now_dt.isoformat(timespec="seconds")
        meta = []
        try:
            tok = tok or token()
            meta = fetch(tok)
            rows = [row(m, now) for m in meta]
            down = False
        except urllib.error.HTTPError as e:
            tok = None                                    # token may have been rewritten; re-read next time
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", "http %s" % e.code])]
        except Exception as e:
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", str(e)[:40]])] if not down else []
            down = True                                   # log the first failure of an outage, not every poll
        write(rows)
        ld = []
        try:
            ld = load.sample(now)
            write_load(ld)
        except Exception:
            pass                                              # load stats are a bonus; never let them stop the viewer log
        try:
            inc.check(meta, now_dt, ld)
        except Exception:
            pass
        time.sleep(POLL)


def summary():
    if not os.path.exists(CSV):
        print("no data yet"); return
    by = {}
    for r in csv.DictReader(open(CSV)):
        by.setdefault((r["session"], r["user"], r["device"]), []).append(r)
    print("%-19s %-8s %4s %-14s %-18s %-9s %-6s %-26s %s" % ("start", "session", "live", "user", "device", "product", "remote", "title/channel", "secs(approx)"))
    for (sid, user, dev), rs in by.items():
        print("%-19s %-8s %4s %-14s %-18s %-9s %-6s %-26s %d" % (rs[0]["ts"], sid, rs[0]["live"], user[:14], dev[:18], rs[0]["product"][:9],
              rs[0]["remote"], (rs[-1]["channel"] or rs[-1]["title"])[:26], len(rs) * POLL))


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else summary() if "--summary" in sys.argv else run()
