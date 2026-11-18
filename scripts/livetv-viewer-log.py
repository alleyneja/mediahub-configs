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
import csv, glob, json, os, re, subprocess, sys, time, urllib.error, urllib.request
from datetime import datetime

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


def run():
    tok, down = None, False
    load = Load()
    while True:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            tok = tok or token()
            rows = [row(m, now) for m in fetch(tok)]
            down = False
        except urllib.error.HTTPError as e:
            tok = None                                    # token may have been rewritten; re-read next time
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", "http %s" % e.code])]
        except Exception as e:
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", str(e)[:40]])] if not down else []
            down = True                                   # log the first failure of an outage, not every poll
        write(rows)
        try:
            write_load(load.sample(now))
        except Exception:
            pass                                              # load stats are a bonus; never let them stop the viewer log
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
    summary() if "--summary" in sys.argv else run()
