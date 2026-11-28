#!/usr/bin/env python3
"""livetv-shadow.py - while someone watches a channel on Xtreme, pull the SAME channel
from Digitalizard for the whole time, so every minute of real viewing gets a
side-by-side comparison (alleyneja/mediahub-issues#87).

Why: the 15-minute A/B cron (livetv-provider-ab.py cycle) covers ~13% of the clock and
<1% per channel, and it has to borrow Xtreme's 2nd slot. Shadowing uses NO Xtreme slot:
the Xtreme side is already recorded second-by-second by livetv-session-tap.py on r9
(what the viewer actually received) and the Plex buffering notices in #live-tv. This
script records the Digitalizard side for the same channel at the same seconds.

Reading the two together:
  both stall at the same moment on a shared-source channel (ESPN, FS1, WOFL, WFTV) -> upstream
  only Xtreme stalls                                                          -> Xtreme
  neither stalls, yet Plex buffered                                           -> Plex / r9 / network

How it knows what is being watched:
  * Plex on r9 /status/sessions -> is anyone watching Live TV (no token needed on the LAN)
  * Threadfin's tune log (docker logs threadfin) -> which channel, and which provider host
  * Xtreme channel -> Digitalizard channel by tvg-id (Xtreme "espn.us" = DL "dl.espn.us")

Digitalizard allows ONE connection, and the A/B test once knocked a real viewer off it.
Rules that protect a viewer:
  * never starts if a viewer tuned a Digitalizard channel during this viewing period,
    or if DL already reports an active connection
  * stops at once when Threadfin shows a new Digitalizard tune
  * after any drop: waits for the provider to clear our old connection, then checks
    active_cons; if someone else holds the slot it gives up instead of reconnecting
    (a reconnect could kick the viewer)
The 15-minute A/B cron skips while this holds the DL slot (it sees DL busy), which also
keeps Xtreme's 2nd slot free during viewing.

Logs (~/logs/livetv-shadow/):
  seconds.csv   ts,xtreme_channel,dl_channel,kB,media_s,note   one row per second while pulling
  sessions.csv  one row per shadow session: simulated-player stalls, longest gap, end reason
STOP file = kill switch. Exits on its own after the Digitalizard trial ends (END below).

Usage:  livetv-shadow.py              run forever (systemd on production, needs docker + Threadfin conf)
        livetv-shadow.py --summary    sessions, stalls, coverage
"""
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

CONF = "/srv/docker/threadfin/conf"
ENV = os.path.join(os.path.dirname(os.path.realpath(__file__)), "../stacks/threadfin/.env")
DIR = os.path.expanduser("~/logs/livetv-shadow")
SECONDS_CSV = os.path.join(DIR, "seconds.csv")
SESSIONS_CSV = os.path.join(DIR, "sessions.csv")
STOP = os.path.join(DIR, "STOP")
PLEX = "http://192.168.0.22:32400/status/sessions"
DL_HOST = "http://zfiqyzhr.lzrdns3.com"
UA = "VLC/3.0.20 LibVLC/3.0.20"  # Digitalizard resets curl/urllib default UAs
END = datetime(2026, 12, 27, 12, 0)  # DL trial expires 2026-12-27 13:14
POLL = 10
START_BUF, REBUF, GAP_S = 6.0, 3.0, 8.0  # same simulated player as livetv-provider-ab.py
MAX_BYTES = 200 * 1024 * 1024
TUNE_RE = re.compile(r"^(\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) \[Threadfin\] (Channel Name|Streaming URL):\s+(.*)$")


def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def say(msg):
    print(f"{now_s()} {msg}", flush=True)


def load_env():
    env = {}
    with open(ENV) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k] = v
    return env


def dl_active_cons(env):
    u = f"{DL_HOST}/player_api.php?username={env['DL_USERNAME']}&password={env['DL_PASSWORD']}"
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.load(r)["user_info"]["active_cons"])


def live_viewers():
    req = urllib.request.Request(PLEX, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        mc = json.load(r)["MediaContainer"]
    return sum(1 for m in mc.get("Metadata", []) if m.get("live"))


def channel_map():
    """Xtreme channel name -> (DL name, DL url), joined on tvg-id."""
    dl = {}
    lines = open(f"{CONF}/dl_live.m3u", encoding="utf-8").read().splitlines()
    for i, l in enumerate(lines):
        m = re.search(r'tvg-id="dl\.([^"]+)"', l)
        if l.startswith("#EXTINF") and m and i + 1 < len(lines):
            dl.setdefault(m.group(1).lower(), (l.rsplit(",", 1)[1].strip(), lines[i + 1].strip()))
    raw = json.load(open(f"{CONF}/xepg.json"))
    out = {}
    for v in raw.get("epg", raw).values():
        tid = (v.get("tvg-id") or "").lower()
        if not v["name"].startswith("DL ") and tid in dl:
            out[v["name"]] = dl[tid]
    return out


def tunes(since):
    """[(datetime, channel name, 'xtreme'|'dl')] from Threadfin's log."""
    p = subprocess.run(["docker", "logs", "--since", since, "threadfin"], capture_output=True, text=True, timeout=30)
    out, name = [], None
    for line in (p.stdout + p.stderr).splitlines():
        m = TUNE_RE.match(line)
        if not m:
            continue
        if m.group(2) == "Channel Name":
            name = m.group(3).strip()
        elif name:
            prov = "dl" if "lzrdns3" in m.group(3) else "xtreme"
            out.append((datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S"), name, prov))
            name = None
    return out


def append_csv(path, fields, rows):
    if not rows:
        return
    os.makedirs(DIR, exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
        os.replace(path, path + ".1")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(fields)
        w.writerows(rows)


class Shadow(threading.Thread):
    """Holds one Digitalizard stream, logs bytes and PCR media seconds per wall second."""

    def __init__(self, env, xname, dname, url):
        super().__init__(daemon=True)
        self.env, self.xname, self.dname, self.url = env, xname, dname, url
        self.stop_ev = threading.Event()
        self.reconnects = 0
        self.end_reason = ""
        self.per_sec = []  # (t, bytes, media_s) per wall second, for the session summary
        self.started = time.time()

    def stop(self, reason):
        if not self.end_reason:
            self.end_reason = reason
        self.stop_ev.set()

    def run(self):
        sec_bytes, sec_media, sec_note = 0, 0.0, "connect"
        sec_t = int(time.time())
        rows = []
        last_pcr, pcr_pid, rem = None, None, b""

        def flush_second(t):
            nonlocal sec_bytes, sec_media, sec_note, rows
            rows.append([datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"), self.xname, self.dname,
                         round(sec_bytes / 1024), round(sec_media, 2), sec_note])
            self.per_sec.append((t, sec_bytes, sec_media))
            sec_bytes, sec_media, sec_note = 0, 0.0, ""
            if len(rows) >= 30:
                append_csv(SECONDS_CSV, ["ts", "xtreme_channel", "dl_channel", "kB", "media_s", "note"], rows)
                rows = []

        while not self.stop_ev.is_set():
            try:
                req = urllib.request.Request(self.url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    last_pcr = None
                    while not self.stop_ev.is_set():
                        chunk = r.read1(65536)
                        t = int(time.time())
                        while sec_t < t:  # close out finished seconds (including silent ones)
                            flush_second(sec_t)
                            sec_t += 1
                        if not chunk:
                            raise EOFError("server closed stream")
                        sec_bytes += len(chunk)
                        data = rem + chunk
                        i = data.find(b"\x47")
                        while 0 <= i and i + 188 <= len(data):
                            if data[i] != 0x47:
                                i = data.find(b"\x47", i + 1)
                                continue
                            if (data[i + 3] & 0x20) and data[i + 4] >= 7 and (data[i + 5] & 0x10):
                                pid = ((data[i + 1] & 0x1F) << 8) | data[i + 2]
                                if pcr_pid is None:
                                    pcr_pid = pid
                                if pid == pcr_pid:
                                    q = data[i + 6:i + 11]
                                    pcr = ((q[0] << 25) | (q[1] << 17) | (q[2] << 9) | (q[3] << 1) | (q[4] >> 7)) / 90000.0
                                    if last_pcr is not None and 0 < pcr - last_pcr < 5:
                                        sec_media += pcr - last_pcr
                                    last_pcr = pcr
                            i += 188
                        rem = data[i:] if i >= 0 else b""
            except Exception as e:  # noqa: BLE001
                if self.stop_ev.is_set():
                    break
                sec_note = f"drop:{type(e).__name__}"[:40]
                # Our dead connection lingers in the provider's count for ~5 s. Wait it out,
                # then reconnect only if nobody else has taken the single slot.
                if self.stop_ev.wait(8):
                    break
                try:
                    busy = dl_active_cons(self.env)
                except Exception:  # noqa: BLE001
                    busy = 1
                if busy:
                    self.stop("dl_slot_taken_after_drop")
                    break
                self.reconnects += 1
                sec_note = "reconnect"
        t = int(time.time())
        while sec_t <= t:
            flush_second(sec_t)
            sec_t += 1
        append_csv(SECONDS_CSV, ["ts", "xtreme_channel", "dl_channel", "kB", "media_s", "note"], rows)
        self.write_session()

    def write_session(self):
        ps = self.per_sec
        if not ps:
            return
        # longest run of seconds with zero bytes
        longest = run = gaps = 0
        for _, b, _ in ps:
            run = run + 1 if b == 0 else 0
            if run == GAP_S:
                gaps += 1
            longest = max(longest, run)
        # simulated player on the media clock, 1 s steps
        playing, started, buf, stalls, stall_s, stall_times = False, False, 0.0, 0, 0, []
        for t, _, media in ps:
            buf += media
            if playing:
                buf -= 1.0
                if buf <= 0:
                    playing, buf = False, 0.0
                    stalls += 1
                    stall_times.append(datetime.fromtimestamp(t).strftime("%H:%M:%S"))
            elif buf >= (REBUF if started else START_BUF):
                playing = started = True
            elif started:
                stall_s += 1
        first = datetime.fromtimestamp(ps[0][0]).strftime("%Y-%m-%d %H:%M:%S")
        last = datetime.fromtimestamp(ps[-1][0]).strftime("%Y-%m-%d %H:%M:%S")
        append_csv(SESSIONS_CSV,
                   ["start", "end", "seconds", "xtreme_channel", "dl_channel", "stall_events", "stall_s",
                    "stall_times", "longest_gap_s", "gaps_8s", "mean_kBps", "reconnects", "end_reason"],
                   [[first, last, len(ps), self.xname, self.dname, stalls, stall_s, " ".join(stall_times),
                     longest, gaps, round(sum(b for _, b, _ in ps) / 1024 / len(ps)), self.reconnects,
                     self.end_reason or "ended"]])
        say(f"session end {self.xname} -> {self.dname}: {len(ps)} s, stalls={stalls} ({stall_s} s), "
            f"longest gap {longest} s, reconnects {self.reconnects}, {self.end_reason}")


def run():
    env = load_env()
    cmap = channel_map()
    map_loaded = time.time()
    say(f"started; {len(cmap)} Xtreme channels have a Digitalizard twin")
    hist = tunes("12h")
    period_start = None      # when the current viewing period began (viewers went 0 -> >0)
    shadow = None
    last_dl_check = 0
    last_wait_msg = ""
    while True:
        if os.path.exists(STOP) or datetime.now() >= END:
            if shadow:
                shadow.stop("stop_file" if os.path.exists(STOP) else "trial_ended")
                shadow.join(30)
            say("STOP file or trial end, exiting")
            return
        try:
            if time.time() - map_loaded > 3600:  # playlists refresh nightly
                cmap, map_loaded = channel_map(), time.time()
            viewers = live_viewers()
            hist = (hist + tunes(f"{POLL + 5}s"))[-500:]
        except Exception as e:  # noqa: BLE001
            say(f"poll error: {e}")
            time.sleep(POLL)
            continue
        seen = set()
        hist = [h for h in hist if not (h in seen or seen.add(h))]
        if viewers and period_start is None:
            # Plex reports the session a few seconds after Threadfin logs the tune, so the
            # viewing period starts at the earliest tune in the last 2 minutes.
            recent = [h[0] for h in hist if (datetime.now() - h[0]).total_seconds() < 120]
            period_start = min(recent or [datetime.now()])
        if not viewers:
            period_start = None
        period = [h for h in hist if period_start and h[0] >= period_start]
        latest = hist[-1] if hist else None
        # A viewer is (or may be) on Digitalizard if they tuned DL this period, or if the
        # most recent tune of all is DL (covers a session already running at startup).
        dl_viewer = bool(viewers) and (any(p == "dl" for _, _, p in period) or (latest is not None and latest[2] == "dl"))
        # The channel to shadow is the most recent tune, and it has to be an Xtreme one.
        current = latest if viewers and latest and latest[2] == "xtreme" else None

        # stop conditions
        if shadow:
            reason = None
            if not shadow.is_alive():
                shadow = None
            elif not viewers:
                reason = "viewer_left"
            elif latest and latest[2] == "dl" and latest[0] >= datetime.fromtimestamp(shadow.started - 2):
                reason = "viewer_tuned_dl"
            elif dl_viewer:
                reason = "viewer_on_dl"
            elif not current or current[1] != shadow.xname:
                reason = "channel_change"
            if reason and shadow:
                shadow.stop(reason)
                shadow.join(30)
                shadow = None
                if reason in ("viewer_tuned_dl", "viewer_on_dl"):
                    say(f"yielding DL slot: {reason}")

        # start conditions
        if not shadow and viewers and current and not dl_viewer and current[1] in cmap:
            if time.time() - last_dl_check >= 30:
                last_dl_check = time.time()
                try:
                    busy = dl_active_cons(env)
                except Exception as e:  # noqa: BLE001
                    say(f"DL API error: {e}")
                    busy = 1
                if busy:
                    msg = f"want to shadow {current[1]} but DL reports {busy} active connection(s); waiting"
                    if msg != last_wait_msg:  # log once per state, not every 30 s
                        say(msg)
                        last_wait_msg = msg
                else:
                    last_wait_msg = ""
                    dname, url = cmap[current[1]]
                    shadow = Shadow(env, current[1], dname, url)
                    shadow.start()
                    say(f"shadowing {current[1]} -> {dname} ({viewers} live viewer(s))")
        time.sleep(POLL)


def summary():
    if not os.path.exists(SESSIONS_CSV):
        print("no sessions yet")
        return
    rows = list(csv.DictReader(open(SESSIONS_CSV)))
    secs = sum(int(r["seconds"]) for r in rows)
    print(f"{len(rows)} shadow sessions, {secs / 3600:.1f} h of Digitalizard pulled alongside real viewing\n")
    print(f"{'start':20}{'min':>5}  {'channel':30}{'stalls':>7}{'stall s':>8}{'gap':>5}{'kB/s':>6}  end")
    for r in rows:
        print(f"{r['start']:20}{int(r['seconds']) // 60:5}  {r['xtreme_channel'][:29]:30}{r['stall_events']:>7}"
              f"{r['stall_s']:>8}{r['longest_gap_s']:>5}{r['mean_kBps']:>6}  {r['end_reason']}"
              + (f"  stalls at {r['stall_times']}" if r["stall_times"] else ""))


if __name__ == "__main__":
    if "--summary" in sys.argv:
        sys.exit(summary())
    sys.exit(run())
