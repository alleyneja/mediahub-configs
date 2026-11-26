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


# ---- incident notice: a live viewer is struggling -> snapshot + one Discord line with a verdict ----
# Triggers: (1) a live session buffering for STALL_SAMPLES consecutive 10 s samples; (2) 3+ buffering reports within 2 min in Plex's own
# log (it timestamps blips to the second; our 10 s poll misses them); (3) the client reports "stopped" within 20 s of a buffering report
# (the Playback Error screen: Plex logs "Client stopped playback"); (4) slow resume: after a pause of RESUME_MIN_PAUSE s or more the
# viewer is not playing again RESUME_SLOW s after resuming. A session that drops out of Plex's list keeps its state for VANISH_GRACE s
# (the app replaces the session on a re-tune), so a missed poll or a vanished session still counts as "still buffering/not playing yet".
# One notice per device per kind per 15 min.
INCIDENT_DIR = os.environ.get("VIEWER_INCIDENTS", os.path.expanduser("~/logs/livetv-incidents"))
WEBHOOKS = [(os.path.expanduser("~/mediahub-configs/scripts/livetv-alerts.env"), "LIVETV_WEBHOOK"),                      # #live-tv (preferred)
            (os.path.expanduser("~/mediahub-configs/scripts/fleet-healthcheck.env"), "FLEET_HEALTH_WEBHOOK")]       # #service-outages (fallback)
TAP_CSV = os.path.expanduser("~/logs/livetv-session-tap/tap.csv")
PLEX_LOG = "/srv/docker/plex/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log"
STALL_SAMPLES = int(os.environ.get("VIEWER_STALL_SAMPLES", "3"))
COOLDOWN = int(os.environ.get("VIEWER_COOLDOWN", "900"))
VANISH_GRACE = int(os.environ.get("VIEWER_VANISH_GRACE", "120"))     # keep a device's state this long after its session leaves /status/sessions
RESUME_MIN_PAUSE = int(os.environ.get("VIEWER_RESUME_MIN_PAUSE", "30"))
RESUME_SLOW = int(os.environ.get("VIEWER_RESUME_SLOW", "15"))
SECRET = re.compile(r"(X-Plex-Token|token|password|username)=[^&\s\"']+", re.I)
URLS = re.compile(r"https?://\S+")
TL = re.compile(r"^(\w{3} \d\d, \d{4} \d\d:\d\d:\d\d)\.\d+ .*Client \[([^\]]+)\] reporting timeline state (\w+), progress of (\d+)/\d+ms .*key=(/livetv/\S*)")

DEAD = re.compile(r"^(\w{3} \d\d, \d{4} \d\d:\d\d:\d\d)\.\d+ .*Completed: \[[^\]]+\] 404 GET /livetv/sessions/[0-9a-f-]+/([0-9a-f]+)-com-plexapp-\w+/index\.m3u8")


def redact(line):
    return URLS.sub("<url>", SECRET.sub(r"\1=***", line))


def tail_lines(path, nbytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - nbytes))
            return f.read().decode(errors="ignore").splitlines()
    except Exception:
        return []


def feed_stats(end_dt, span=90):
    """Streams the stream-logger saw in the 10 min before end_dt -> [(mean kB/s in [end-span, end+10 s], longest silent gap s, gap start HH:MM:SS)].
    Ignores idle connections (e.g. tailscaled)."""
    lo = (end_dt - timedelta(seconds=span)).isoformat(timespec="seconds")
    hi = (end_dt + timedelta(seconds=10)).isoformat(timespec="seconds")
    cut600 = (end_dt - timedelta(seconds=600)).isoformat(timespec="seconds")
    by = collections.defaultdict(list)
    for l in tail_lines(TAP_CSV, 800000):
        p = l.split(",")
        if len(p) >= 3 and cut600 <= p[0] <= hi:
            try:
                by[p[1]].append((p[0], float(p[2])))
            except ValueError:
                pass
    out = []
    for c, rs in by.items():
        if sum(k for _, k in rs) < 3000:                    # < ~3 MB in 10 min: not a stream
            continue
        recent = [(t, k) for t, k in rs if t >= lo]
        if len(recent) < 15:
            continue
        gap = run = 0; gstart = rstart = ""
        for t, k in recent:
            if k < 50:
                if run == 0:
                    rstart = t
                run += 1
                if run > gap:
                    gap, gstart = run, rstart
            else:
                run = 0
        out.append((sum(k for _, k in recent) / len(recent), gap, gstart[11:19]))
    return out


def snapshot(dev, now_dt):
    name = now_dt.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"\W+", "_", dev)[:20]
    d = os.path.join(INCIDENT_DIR, name)
    os.makedirs(d, exist_ok=True)
    cutoff = now_dt - timedelta(minutes=12)
    keep, ok = [], False
    for l in tail_lines(PLEX_LOG, 12 * 1024 * 1024):          # Plex logs rotate in ~20-45 min: copy the last 12 min now, credentials removed
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


def path_loss(addr):
    """Read r9's own TCP stats for the biggest media connection to this remote viewer: share of bytes that had to be resent.
    Cumulative over the connection's life, so it is an average. None when there is no connection to read."""
    if not addr or addr == "?":
        return None
    try:
        out = subprocess.run(["ss", "-tin", "state", "established", "(", "sport", "=", ":32400", ")", "dst", addr],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    best = None
    for blk in out.split("[::ffff:")[1:]:
        sent = re.search(r"bytes_sent:(\d+)", blk)
        if sent and (best is None or int(sent.group(1)) > best[0]):
            retr = re.search(r"bytes_retrans:(\d+)", blk)
            rtt = re.search(r" rtt:([\d.]+)", blk)
            best = (int(sent.group(1)), int(retr.group(1)) if retr else 0, float(rtt.group(1)) if rtt else None)
    if not best or best[0] < 1_000_000:                      # too little data to say anything
        return None
    return {"pct": 100.0 * best[1] / best[0], "rtt": best[2]}


def verdict(streams, load_row, remote=False, loss=None):
    try:
        cpu, gpu, enc = float(load_row[1] or 0), float(load_row[2] or 0), float(load_row[3] or 0)
    except (ValueError, IndexError):
        cpu = gpu = enc = 0
    if not streams:
        return "no active provider stream was visible to the logger (stream may have dropped)"
    if any(m < 300 or g >= 20 for m, g, _ in streams):
        return "the FEED into r9 stalled (provider side or the route to the provider)"
    if cpu >= 60 or gpu >= 90 or enc >= 90:
        return "r9 was BUSY (CPU/GPU high)"
    g = max((g for _, g, _ in streams), default=0)
    note = " (longest feed gap %d s, a routine size: this provider has one about every 2 min)" % g if g >= 8 else ""
    if remote:
        if loss is None:
            ltxt = "no connection stats were available"
        else:
            ltxt = "about %.1f%% of the data r9 sent this viewer was lost and had to be resent%s" % (loss["pct"], (" (round trip %.0f ms)" % loss["rtt"]) if loss.get("rtt") else "")
            if loss["pct"] >= 0.5:
                ltxt += ", which leaves little headroom for a video stream"
        return ("the feed and r9 look fine%s, so the cause is the remote internet path to this viewer (their home internet or Wi-Fi), "
                "not the server. %s" % (note, ltxt[0].upper() + ltxt[1:]))
    return ("the feed and r9 look fine%s, so the cause is on the viewer's side: their device or Wi-Fi, or the app restarting the stream "
            "after a pause or a fresh join" % note)


def build_message(m, now_dt, load_row, streams, folder, headline, extra=""):
    p, t = m.get("Player", {}), m.get("TranscodeSession", {}) or {}
    feeds = ", ".join("%.0f kB/s%s" % (mean, (", longest silent gap %d s at %s" % (gap, gs)) if gap >= 8 else "") for mean, gap, gs in streams) or "none seen"
    spd = ("%.1f" % float(t["speed"])) if t.get("speed") not in (None, "") else "?"
    return ("Live TV: %s (%s) on \"%s\": %s.%s\nFeed into r9: %s. r9: CPU %s%%, GPU %s%%. Transcode %s x real time.\nVerdict: %s.\nSnapshot on r9: %s" % (
        p.get("title", "?"), "remote" if not p.get("local") else "home network", (m.get("title") or "?")[:40], headline, extra, feeds,
        load_row[1] if len(load_row) > 1 else "?", load_row[2] if len(load_row) > 2 and load_row[2] != "" else "?", spd, verdict(streams, load_row, not p.get("local"), path_loss(p.get("remotePublicAddress") or p.get("address")) if not p.get("local") else None),
        folder.replace(os.path.expanduser("~"), "~")))


def post_discord(msg):
    for path, var in WEBHOOKS:
        try:
            hook = next(l.split("=", 1)[1].strip().strip("\"'") for l in open(path) if l.startswith(var + "="))
            req = urllib.request.Request(hook, data=json.dumps({"content": msg[:1900]}).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "livetv-viewer-log/1.0"})
            urllib.request.urlopen(req, timeout=8)
            return True
        except Exception:
            continue                                            # missing file/var or a failed post: try the next destination
    return False


class Incidents:
    def __init__(self, live=True):
        self.live = live                                       # False = replay/test: build messages but never snapshot or post
        self.streak, self.last, self.joined, self.blips, self.alias = {}, {}, {}, {}, {}
        self.dev = {}                                          # device -> {bstart, pstart, resume, lastseen, m}
        self.pos = self.ino = None
        self.out = []

    def _cool(self, key, ts, span=None):
        if ts - self.last.get(key, 0) <= (COOLDOWN if span is None else span):
            return False
        self.last[key] = ts
        return True

    def _aliases(self, meta):
        for m in meta:
            if not m.get("live"):
                continue
            p, s = m.get("Player", {}), m.get("Session", {})
            ids = {p.get("machineIdentifier"), s.get("id")}
            try:
                ids.add(m["Media"][0]["Part"][0]["key"].split("/")[4])      # live client-session id, used by some apps in timeline reports
            except Exception:
                pass
            for i in ids:
                if i:
                    self.alias[i] = (p.get("title", "?"), m)

    def _lookup(self, cid):
        if cid in self.alias:
            return self.alias[cid]
        return next((v for k, v in self.alias.items() if k[:8] == cid[:8]), None)

    def _fire(self, dev, m, when, now_dt, ld, headline, extra=""):
        msg_streams = feed_stats(when)
        folder = snapshot(dev, now_dt) if self.live else "(not saved)"
        msg = build_message(m, when, ld, msg_streams, folder, headline, extra)
        self.out.append(msg)
        if self.live:
            try:
                with open(os.path.join(INCIDENT_DIR, "incidents.log"), "a") as f:
                    f.write(now_dt.isoformat(timespec="seconds") + " " + msg.replace("\n", " | ") + "\n")
            except Exception:
                pass
            post_discord(msg)

    def check(self, meta, now_dt, ld, api_ok=True):
        now_ts = now_dt.timestamp()
        self._aliases(meta)
        seen = set()
        for m in meta:                                          # (1)+(4) per-device timers; keyed by Player title
            if not m.get("live"):
                continue
            k = m.get("Player", {}).get("title", "?")
            state = m.get("Player", {}).get("state")
            seen.add(k)
            self.joined.setdefault(k, now_dt)
            d = self.dev.setdefault(k, {"bstart": None, "pstart": None, "resume": None})
            d["lastseen"], d["m"] = now_ts, m
            if state != "paused" and d["pstart"] and now_ts - d["pstart"] >= 10:
                d["last_pause"] = (now_ts, int(now_ts - d["pstart"]))
            if state == "paused":
                d["bstart"], d["resume"] = None, None
                d["pstart"] = d["pstart"] or now_ts
            elif state == "buffering":
                d["bstart"] = d["bstart"] or now_ts
                if d["pstart"] and not d["resume"] and now_ts - d["pstart"] >= RESUME_MIN_PAUSE:
                    d["resume"] = (now_ts, int(now_ts - d["pstart"]))   # first non-paused sample after a real pause = the resume
                d["pstart"] = None
            else:                                                 # playing: all clear
                d["bstart"] = d["pstart"] = d["resume"] = None
        for k in list(self.dev):                                  # devices missing from this poll
            if k in seen:
                continue
            d = self.dev[k]
            if not api_ok:                                        # Plex did not answer: not a vanished session, hold state
                d["lastseen"] = now_ts
            elif now_ts - d["lastseen"] > VANISH_GRACE:
                for dd in (self.dev, self.joined, self.streak):
                    dd.pop(k, None)
        for k, d in self.dev.items():
            m, gone = d.get("m"), k not in seen
            if not m or not api_ok:                               # no verdicts while Plex itself is not answering
                continue
            if d["bstart"] and now_ts - d["bstart"] >= (STALL_SAMPLES - 1) * POLL and self._cool((k, "long"), now_ts):
                self._fire(k, m, now_dt, now_dt, ld, "buffering for %d s+ at %s%s" % (now_ts - d["bstart"] + POLL, now_dt.strftime("%H:%M:%S"),
                           " (then the session dropped out of Plex's list)" if gone else ""), self._ctx(k, m, meta, now_dt))
                d["resume"] = None                                # one notice covers it
            elif d["resume"] and now_ts - d["resume"][0] >= RESUME_SLOW:
                if self._cool((k, "resume"), now_ts):
                    self._fire(k, m, now_dt, now_dt, ld, "slow resume at %s: paused %d s, still not playing %d s after resuming (%s)" % (
                        now_dt.strftime("%H:%M:%S"), d["resume"][1], now_ts - d["resume"][0],
                        "session gone from Plex's list" if gone else "state: %s" % m.get("Player", {}).get("state")), self._ctx(k, m, meta, now_dt))
                d["resume"] = None
        self.events(self._new_log_events(), meta, now_dt, ld)

    def _pause_note(self, dev, ts):
        lp = (self.dev.get(dev) or {}).get("last_pause")
        if lp and 0 <= ts.timestamp() - lp[0] <= 300:
            return " It had resumed after a %d s pause %d s earlier." % (lp[1], ts.timestamp() - lp[0])
        return ""

    def _ctx(self, dev, m, meta, now_dt):
        j = self.joined.get(dev)
        others = sum(1 for x in meta if x.get("live") and x is not m and x.get("title") == m.get("title"))
        return " Joined live %s earlier; %d other viewer%s on this program." % (
            ("%d s" % (now_dt - j).total_seconds()) if j else "?", others, "" if others == 1 else "s")

    def _new_log_events(self):
        try:
            st = os.stat(PLEX_LOG)
        except OSError:
            return []
        if self.ino is None:
            self.ino, self.pos = st.st_ino, st.st_size            # first run: start at the end, never replay history
            return []
        if self.ino != st.st_ino or st.st_size < self.pos:
            self.ino, self.pos = st.st_ino, 0                      # Plex rotated its log
        with open(PLEX_LOG, "rb") as f:
            f.seek(self.pos); data = f.read(4000000); self.pos = f.tell()
        ev = []
        for l in data.decode(errors="ignore").splitlines():
            mm = TL.match(l)
            if mm:
                ev.append((datetime.strptime(mm.group(1), "%b %d, %Y %H:%M:%S"), mm.group(2), mm.group(3), int(mm.group(4))))
                continue
            mm = DEAD.match(l)
            if mm:
                ev.append((datetime.strptime(mm.group(1), "%b %d, %Y %H:%M:%S"), mm.group(2), "dead404", 0))
        return ev

    def events(self, evs, meta, now_dt, ld):
        for ts, cid, state, prog in evs:                         # (2) repeated blips, (3) stop right after a blip
            hit = self._lookup(cid)
            dev, m = hit if hit else ("device " + cid[:8], {"Player": {"title": "device " + cid[:8], "local": True}, "title": "?"})
            L = self.blips.setdefault(cid, [])
            if state == "dead404":                                 # (5) the stream died for this viewer
                if self._cool((dev, "dead"), ts.timestamp(), 120):
                    self._fire(dev, m, ts, now_dt, ld, "STREAM DIED at %s: Plex answered 404 (\"Failed to find consumer\") for this viewer's live session; "
                               "the app shows Playback Error and Retry or re-selecting the same channel will not recover it. "
                               "What works (tested on the Fire Stick): Close, open a DIFFERENT channel, then come back to this one" % ts.strftime("%H:%M:%S"),
                               (self._ctx(dev, m, meta, now_dt) if hit else "") + self._pause_note(dev, ts))
                continue
            if state == "buffering":
                if prog < 15000:
                    continue                                       # normal start-up buffering in the first 15 s of a stream
                if not L or (ts - L[-1]).total_seconds() >= 3:
                    L.append(ts)
                L[:] = [t for t in L if (ts - t).total_seconds() <= 120]
                if len(L) >= 3 and self._cool((dev, "repeat"), ts.timestamp()):
                    self._fire(dev, m, ts, now_dt, ld, "%d buffering blips in %d s (last at %s)" % (len(L), (L[-1] - L[0]).total_seconds(), ts.strftime("%H:%M:%S")),
                               self._ctx(dev, m, meta, now_dt) if hit else "")
            elif state == "stopped" and L and (ts - L[-1]).total_seconds() <= 20:
                if ts.timestamp() - self.last.get((dev, "repeat"), 0) <= 90:
                    msg = "Live TV: %s then STOPPED playback at %s, right after buffering. That is the Playback Error on screen. Details in the notice above." % (dev, ts.strftime("%H:%M:%S"))
                    self.out.append(msg)
                    if self.live:
                        post_discord(msg)
                elif self._cool((dev, "stop"), ts.timestamp()):
                    self._fire(dev, m, ts, now_dt, ld, "playback STOPPED at %s, %d s after buffering (likely the Playback Error screen)" % (ts.strftime("%H:%M:%S"), (ts - L[-1]).total_seconds()),
                               self._ctx(dev, m, meta, now_dt) if hit else "")
                L.clear()


def replay(start, end):
    """Replay Plex's log for HH:MM:SS..HH:MM:SS today and print the notices that would have fired. Posts nothing, saves nothing."""
    files = sorted(glob.glob(os.path.dirname(PLEX_LOG) + "/Plex Media Server*.log*"), key=lambda f: (0 if f.endswith("log") else int(f.rsplit(".", 2)[-2]) if f.rsplit(".", 2)[-2].isdigit() else 99), reverse=True)
    day = datetime.now().strftime("%b %d, %Y")
    ev = []
    for f in files:
        for l in tail_lines(f, 20 * 1024 * 1024):
            mm = TL.match(l)
            if mm and mm.group(1).startswith(day) and start <= mm.group(1)[13:] <= end:
                ev.append((datetime.strptime(mm.group(1), "%b %d, %Y %H:%M:%S"), mm.group(2), mm.group(3), int(mm.group(4))))
    ev.sort()
    inc = Incidents(live=False)
    for row_ in csv.DictReader(open(CSV)):                       # device names from the viewer log (session id prefix = client id prefix)
        if row_["live"] == "1" and start <= row_["ts"][11:19] <= end:
            inc.alias.setdefault(row_["session"], (row_["device"], {"Player": {"title": row_["device"], "local": row_["remote"] != "1"}, "title": row_["title"]}))
    print("%d live timeline events between %s and %s" % (len(ev), start, end))
    inc.events(ev, [], datetime.now(), ["", "3", "0", "0"])
    print("\n\n".join(inc.out) if inc.out else "(no notice would have fired)")


def selftest():
    """Build (and save a snapshot for) a notice from the current state without posting."""
    now = datetime.now()
    meta = fetch(token())
    m = next((x for x in meta if x.get("live")), meta[0] if meta else {"Player": {"title": "(no session)", "local": True}, "title": "(none)"})
    ld = Load(); time.sleep(2); row_ = ld.sample(now.isoformat(timespec="seconds"))
    d = snapshot("selftest", now)
    print(build_message(m, now, row_, feed_stats(now), d, "(self-test) buffering for 30 s+ at %s" % now.strftime("%H:%M:%S")))
    print("snapshot files:", ", ".join("%s %dKB" % (f, os.path.getsize(os.path.join(d, f)) // 1024) for f in sorted(os.listdir(d))))
    shutil.rmtree(d, ignore_errors=True)


def run():
    tok, down = None, False
    load = Load()
    inc = Incidents()
    while True:
        now_dt = datetime.now()
        now = now_dt.isoformat(timespec="seconds")
        meta, api_ok = [], True
        try:
            tok = tok or token()
            meta = fetch(tok)
            rows = [row(m, now) for m in meta]
            down = False
        except urllib.error.HTTPError as e:
            tok, api_ok = None, False                     # token may have been rewritten; re-read next time
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", "http %s" % e.code])]
        except Exception as e:
            rows = [pad([now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", str(e)[:40]])] if not down else []
            down, api_ok = True, False                    # log the first failure of an outage, not every poll
        write(rows)
        ld = []
        try:
            ld = load.sample(now)
            write_load(ld)
        except Exception:
            pass                                              # load stats are a bonus; never let them stop the viewer log
        try:
            inc.check(meta, now_dt, ld, api_ok)
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
    if "--replay" in sys.argv:
        i = sys.argv.index("--replay"); replay(sys.argv[i + 1], sys.argv[i + 2])
    else:
        selftest() if "--selftest" in sys.argv else summary() if "--summary" in sys.argv else run()
