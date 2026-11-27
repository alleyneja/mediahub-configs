#!/usr/bin/env python3
"""
Head-to-head feed comparison: Xtreme HD IPTV vs Digitalizard, same channel,
pulled at the same time from production. Issue: alleyneja/mediahub-issues#87.

Question it answers: are the Live TV stalls the provider or our setup? If both
providers are pulled over the same line at the same moment and one stalls while
the other doesn't, the difference is the provider. If both stall together, it's
our side (line/ISP) or something shared.

What it measures (per provider, per pull):
  - media seconds delivered vs wall-clock seconds, read from the MPEG-TS PCR
    clock inside the stream. Byte rate is misleading here: the providers send
    different bitrates and both deliver in bursts, which a player's buffer hides
    (see #87 Nov 23: ~130 byte gaps, ~3 visible).
  - a simulated player: starts once START_BUF s of media are buffered, plays at
    1x, stalls when the buffer runs dry and resumes after REBUF s are refilled.
    stall_events / stall_s are what a viewer would actually see.
  - longest silent gap (no bytes at all), mean KB/s, time to first byte,
    reconnects (connection dropped mid-pull and had to be reopened).

Connection safety: Xtreme allows 2 connections (family viewing), Digitalizard 1.
A pull only starts when BOTH accounts report active_cons == 0, so it never takes
a slot someone is using; a viewer arriving mid-pull still has Xtreme's 2nd slot.
That means it skips while anyone watches Xtreme (the bias noted in #87).

Usage:
  livetv-provider-ab.py run [--seconds 180] [--channels ESPN,SEC]   one round, every pair
  livetv-provider-ab.py cycle [--seconds 180]                        one pair, next in rotation (for cron)
  livetv-provider-ab.py summary
Log: ~/logs/livetv-provider-ab/pulls.csv   Kill switch: touch ~/logs/livetv-provider-ab/STOP
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

CONF = "/srv/docker/threadfin/conf"
ENV = os.path.join(os.path.dirname(os.path.realpath(__file__)), "../stacks/threadfin/.env")
LOGDIR = os.path.expanduser("~/logs/livetv-provider-ab")
CSV = os.path.join(LOGDIR, "pulls.csv")
UA = "VLC/3.0.20 LibVLC/3.0.20"  # Digitalizard RSTs curl/urllib default UAs

START_BUF = 6.0   # s of media buffered before "playback" starts
REBUF = 3.0       # s refilled before resuming after a stall
GAP_S = 8.0       # silent gap worth counting (same threshold as #87 Nov 23 notes)

# (label, Xtreme name in live_only.m3u, Digitalizard name in dl_live.m3u)
# Ordered by how often the family actually tunes them (Threadfin log, Nov 2026):
# ESPN 67, WESH 66, WOFL 57, WKMG 47, NFLN 35, WFTV 34, SEC 29, WNYW 23, FS1 16, ESPN2 9.
# CNN/FNC/TNT/USA are rarely watched; kept as a non-sports control group.
PAIRS = [
    ("ESPN", "USA ESPN HD", "DL ESPN 1 HD"),
    ("WESH", "USA NBC 2 WESH ORLANDO", "DL NBC 02 (WESH) ORLANDO"),
    ("WOFL", "USA FOX 35 WOFL ORLANDO", "DL FOX 35 (WOFL) ORLANDO"),
    ("WKMG", "USA CBS 6 WKMG ORLANDO", "DL CBS 06 (WKMG) ORLANDO"),
    ("NFLN", "USA NFL Network", "DL NFL NETWORK"),
    ("WFTV", "USA ABC 9 WFTV ORLANDO", "DL ABC 09 (WFTV) ORLANDO"),
    ("SEC", "USA ESPN SEC Network", "DL ESPN SEC NETWORK HD"),
    ("WNYW", "USA FOX 5 WNYW NEW YORK", "DL FOX 05 (WNYW) NEW YORK"),
    ("FS1", "USA FOX SPORTS 1 HD", "DL FOX SPORTS 1 HD"),
    ("ESPN2", "USA ESPN 2", "DL ESPN 2 HD"),
    ("CNN", "USA CNN*", "DL CNN FHD"),
    ("FNC", "USA Fox News Channel", "DL FOX NEWS HD"),
    ("TNT", "USA TNT East FHD*", "DL TNT EAST FHD"),
    ("USA", "USA USA Network East", "DL USA NETWORK EAST HD"),
]

FIELDS = ["ts", "label", "provider", "seconds", "status", "ttfb_s", "kbps", "media_s",
          "longest_gap_s", "gaps_8s", "stall_events", "stall_s", "min_buffer_s",
          "reconnects", "cdn_host", "error"]


def load_env():
    env = {}
    with open(ENV) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.strip().partition("=")
                env[k] = v
    return env


def m3u_urls(path):
    out, name = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF"):
                name = line.rsplit(",", 1)[1].strip()
            elif line and not line.startswith("#") and name:
                out[name] = line
                name = None
    return out


def active_cons(host, user, pw):
    url = f"{host}/player_api.php?username={user}&password={pw}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return int(json.load(r)["user_info"]["active_cons"])


class Pull(threading.Thread):
    """Reads one TS feed for `seconds`, tracking bytes and PCR media time per arrival."""

    def __init__(self, url, seconds):
        super().__init__(daemon=True)
        self.url, self.seconds = url, seconds
        self.events = []          # (wall_t, cumulative_bytes, cumulative_media_s)
        self.ttfb = None
        self.reconnects = 0
        self.cdn_host = ""
        self.error = ""

    def run(self):
        t0 = time.monotonic()
        self.t0 = t0
        total = 0
        media = 0.0
        last_pcr = None
        pcr_pid = None
        rem = b""
        while time.monotonic() - t0 < self.seconds:
            try:
                req = urllib.request.Request(self.url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as r:
                    self.cdn_host = urllib.parse.urlsplit(r.geturl()).hostname or ""
                    last_pcr = None  # new connection: don't bridge PCR across it
                    while time.monotonic() - t0 < self.seconds:
                        chunk = r.read1(65536)
                        now = time.monotonic() - t0
                        if not chunk:
                            raise EOFError("server closed stream")
                        if self.ttfb is None:
                            self.ttfb = now
                        total += len(chunk)
                        data = rem + chunk
                        i = data.find(b"\x47")
                        while 0 <= i and i + 188 <= len(data):
                            if data[i] != 0x47:  # lost sync: find the next sync byte
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
                                        media += pcr - last_pcr  # skip wraps/discontinuities
                                    last_pcr = pcr
                            i += 188
                        rem = data[i:] if i >= 0 else b""
                        self.events.append((now, total, media))
            except Exception as e:  # noqa: BLE001 - record and retry within the window
                if time.monotonic() - t0 >= self.seconds:
                    break
                if self.ttfb is None and not self.events:
                    self.error = f"{type(e).__name__}: {e}"[:120]
                self.reconnects += 1
                time.sleep(1)
        self.events.append((time.monotonic() - t0, total, media))

    def stats(self):
        ev = self.events
        dur = ev[-1][0] if ev else self.seconds
        total = ev[-1][1] if ev else 0
        media = ev[-1][2] if ev else 0.0
        # longest silent gap and gaps >= GAP_S (byte arrivals)
        times = [0.0] + [e[0] for e in ev if e[1] > 0] + [dur]
        gaps = [b - a for a, b in zip(times, times[1:])]
        longest = max(gaps) if gaps else dur
        n_gaps = sum(1 for g in gaps if g >= GAP_S)
        # simulated player on the media clock
        playing, play_pos, stalls, stall_s, min_buf = False, 0.0, 0, 0.0, None
        idx, cur_media, t = 0, 0.0, 0.0
        step = 0.1
        while t < dur:
            while idx < len(ev) and ev[idx][0] <= t:
                cur_media = ev[idx][2]
                idx += 1
            buf = cur_media - play_pos
            if playing:
                if buf <= 0:
                    playing = False
                    stalls += 1
                else:
                    play_pos += step
                    min_buf = buf if min_buf is None else min(min_buf, buf)
            else:
                need = START_BUF if stalls == 0 and play_pos == 0 else REBUF
                if buf >= need:
                    playing = True
                elif play_pos > 0 or stalls:
                    stall_s += step
            t += step
        if not playing and play_pos == 0:
            status = "never_started"
        else:
            status = "ok"
        if self.error and total == 0:
            status = "error"
        return {
            "seconds": round(dur, 1), "status": status,
            "ttfb_s": round(self.ttfb, 2) if self.ttfb is not None else "",
            "kbps": round(total / 1024 / dur) if dur else 0, "media_s": round(media, 1),
            "longest_gap_s": round(longest, 1), "gaps_8s": n_gaps,
            "stall_events": stalls, "stall_s": round(stall_s, 1),
            "min_buffer_s": round(min_buf, 1) if min_buf is not None else "",
            "reconnects": self.reconnects, "cdn_host": self.cdn_host, "error": self.error,
        }


def write_rows(rows):
    os.makedirs(LOGDIR, exist_ok=True)
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def compare(pairs, seconds, xtreme_busy_ok=0):
    env = load_env()
    xt_user, xt_pw = env["IPTV_USERNAME"], env["IPTV_PASSWORD"]
    dl_user, dl_pw = env["DL_USERNAME"], env["DL_PASSWORD"]
    xt = m3u_urls(f"{CONF}/live_only.m3u")
    dl = m3u_urls(f"{CONF}/dl_live.m3u")
    for label, xn, dn in pairs:
        if os.path.exists(os.path.join(LOGDIR, "STOP")):
            print("STOP file present, exiting")
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            xc = active_cons("http://link4tv.cc:80", xt_user, xt_pw)
            dc = active_cons("http://zfiqyzhr.lzrdns3.com", dl_user, dl_pw)
        except Exception as e:  # noqa: BLE001
            write_rows([{"ts": ts, "label": label, "provider": p, "status": "api_error", "error": str(e)[:120]} for p in ("xtreme", "digitalizard")])
            print(f"{ts} {label}: provider API error {e}")
            continue
        if xc > xtreme_busy_ok or dc:
            write_rows([{"ts": ts, "label": label, "provider": p, "status": "skipped_viewer"} for p in ("xtreme", "digitalizard")])
            print(f"{ts} {label}: skipped (xtreme active_cons={xc}, digitalizard={dc})")
            continue
        if xn not in xt or dn not in dl:
            print(f"{ts} {label}: channel missing from playlist ({xn in xt}, {dn in dl})")
            continue
        a, b = Pull(xt[xn], seconds), Pull(dl[dn], seconds)
        a.start(); b.start(); a.join(seconds + 60); b.join(seconds + 60)
        rows = []
        for prov, p in (("xtreme", a), ("digitalizard", b)):
            r = {"ts": ts, "label": label, "provider": prov, **p.stats()}
            rows.append(r)
            print(f"{ts} {label:6} {prov:12} stalls={r['stall_events']} ({r['stall_s']}s) "
                  f"media={r['media_s']}/{r['seconds']}s gap={r['longest_gap_s']}s kbps={r['kbps']} "
                  f"ttfb={r['ttfb_s']} reconn={r['reconnects']} {r['status']} {r['error']}")
        write_rows(rows)
        time.sleep(8)  # providers count a closed connection for ~5 s


def summary():
    rows = list(csv.DictReader(open(CSV)))
    done = [r for r in rows if r["status"] in ("ok", "never_started", "error")]
    skipped = sum(1 for r in rows if r["status"] == "skipped_viewer") // 2
    print(f"{len(done) // 2} paired pulls, {skipped} skipped for viewers, from {rows[0]['ts']} to {rows[-1]['ts']}\n")
    print(f"{'provider':13}{'pulls':>6}{'w/stall':>9}{'stalls':>8}{'stall s':>9}{'gap>=8s':>9}{'worst gap':>10}{'failed':>8}{'reconn':>8}")
    for prov in ("xtreme", "digitalizard"):
        v = [r for r in done if r["provider"] == prov]
        if not v:
            continue
        f = lambda k: [float(r[k]) for r in v if r[k] not in ("", None)]  # noqa: E731
        print(f"{prov:13}{len(v):6}{sum(1 for r in v if int(r['stall_events'] or 0)):9}"
              f"{int(sum(f('stall_events'))):8}{sum(f('stall_s')):9.0f}{int(sum(f('gaps_8s'))):9}"
              f"{max(f('longest_gap_s') or [0]):10.1f}{sum(1 for r in v if r['status'] != 'ok'):8}{int(sum(f('reconnects'))):8}")
    # same-moment agreement: did they stall together (shared path) or apart (provider)?
    by = {}
    for r in done:
        by.setdefault((r["ts"], r["label"]), {})[r["provider"]] = r
    both = only_x = only_d = 0
    for p in by.values():
        if len(p) != 2:
            continue
        sx = int(p["xtreme"]["stall_events"] or 0) > 0 or p["xtreme"]["status"] != "ok"
        sd = int(p["digitalizard"]["stall_events"] or 0) > 0 or p["digitalizard"]["status"] != "ok"
        both += sx and sd
        only_x += sx and not sd
        only_d += sd and not sx
    print(f"\nbad pulls at the same moment: both={both}  only xtreme={only_x}  only digitalizard={only_d}")
    print("\nper channel (pulls with a stall or failure, xtreme / digitalizard):")
    labels = sorted({r["label"] for r in done})
    for lb in labels:
        cnt = {}
        for prov in ("xtreme", "digitalizard"):
            v = [r for r in done if r["label"] == lb and r["provider"] == prov]
            cnt[prov] = (sum(1 for r in v if int(r["stall_events"] or 0) or r["status"] != "ok"), len(v))
        print(f"  {lb:6} {cnt['xtreme'][0]}/{cnt['xtreme'][1]}  vs  {cnt['digitalizard'][0]}/{cnt['digitalizard'][1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["run", "cycle", "summary"])
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--channels", default="")
    # 1 = run even with one Xtreme slot busy (one viewer or a stale connection), taking the
    # 2nd; still skips at 2. Jay's call 2026-11-27, also for the weekend cron: measuring during
    # real viewing (game time) matters more than keeping the 2nd slot free. Cost: during a pull
    # a 2nd viewer on a DIFFERENT channel gets "All tuners in use"; a viewer's mid-stream
    # re-tune could be refused. The viewer already playing is never kicked.
    ap.add_argument("--xtreme-busy-ok", type=int, default=0)
    a = ap.parse_args()
    if a.mode == "summary":
        return summary()
    pairs = PAIRS
    if a.channels:
        want = set(a.channels.upper().split(","))
        pairs = [p for p in PAIRS if p[0] in want]
    if a.mode == "cycle":
        os.makedirs(LOGDIR, exist_ok=True)
        st = os.path.join(LOGDIR, "rotation")
        i = int(open(st).read()) if os.path.exists(st) else 0
        pairs = [PAIRS[i % len(PAIRS)]]
        open(st, "w").write(str(i + 1))
    compare(pairs, a.seconds, a.xtreme_busy_ok)


if __name__ == "__main__":
    sys.exit(main())
