#!/usr/bin/env python3
"""livetv-session-tap.py - passive per-second log of REAL Live TV streams (#87).

Run on r9 (where Plex lives). Threadfin runs with buffer off and hands Plex the provider
URL, so Plex's own TCP connections to the IPTV provider ARE the viewing sessions. This
reads kernel TCP counters via `ss -tin` once a second; it opens no connection of its own,
so it never uses one of the account's 2 slots and it covers 100% of viewing time.

Writes one CSV row per connection per second, only while a stream exists (idle = no rows):
  ts,conn,recv_kBps,rtt_ms,retrans_total,unacked,peer_ip   (peer_ip = the provider video server; logged to see whether it rotates)
Never logs URLs, credentials or remote ports beyond a short connection hash.

Stall rule used by --summary: the feed is BURSTY (several MB every 6-8 s, near-silence between),
so per-second rates are meaningless and bitrates differ per channel. A second is "slow" when the trailing
30 s average is under half of that session's own mean; also reports the longest silent gap and gaps over 15 s.

Usage:  livetv-session-tap.py            run forever (systemd)
        livetv-session-tap.py --summary  print stalls per session from the log
"""
import csv, os, re, subprocess, sys, time, hashlib
from datetime import datetime

PORT = os.environ.get("TAP_PORT", "80")     # Plex's transcoder follows Threadfin's redirect to the provider's video server on :80
PRIVATE = re.compile(r"^(10\.|192\.168\.|127\.|172\.(1[6-9]|2\d|3[01])\.|\[?::)")
LOGDIR = os.environ.get("TAP_LOGDIR", os.path.expanduser("~/logs/livetv-session-tap"))
MAX_BYTES = 200 * 1024 * 1024          # rotate at 200 MB, keep 1 old file
STOP = os.path.join(LOGDIR, "STOP")


def snapshot():
    """Return {conn_id: (bytes_received, rtt_ms, retrans_total, unacked)} for outbound :80 connections to non-LAN hosts."""
    out = {}
    r = subprocess.run(["ss", "-tinH", "state", "established", "(", "dport", "=", ":" + PORT, ")"],
                       capture_output=True, text=True)
    lines = r.stdout.splitlines()
    # ss prints the socket line, then an indented info line
    for i in range(0, len(lines) - 1, 2):
        head, info = lines[i].split(), lines[i + 1]
        if len(head) < 4:
            continue
        local, peer = head[2], head[3]
        if PRIVATE.match(peer):
            continue
        cid = hashlib.sha1((local + peer).encode()).hexdigest()[:6]
        m = re.search(r"bytes_received:(\d+)", info)
        rtt = re.search(r"rtt:([\d.]+)/", info)
        ret = re.search(r"retrans:\d+/(\d+)", info)
        un = re.search(r"unacked:(\d+)", info)
        if not m:
            continue
        out[cid] = (int(m.group(1)), float(rtt.group(1)) if rtt else -1.0,
                    int(ret.group(1)) if ret else 0, int(un.group(1)) if un else 0, peer.rsplit(":", 1)[0])
    return out


def rotate(path):
    if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
        os.replace(path, path + ".1")


def run():
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, "tap.csv")
    prev, prev_t = {}, time.time()
    first, active = {}, set()     # a conn is a "stream" once it has moved >=100 kB (skips idle keepalives)
    while not os.path.exists(STOP):
        now = time.time()
        cur = snapshot()
        dt = max(now - prev_t, 0.001)
        rows = []
        for cid, (b, rtt, ret, un, peer_ip) in cur.items():
            first.setdefault(cid, b)
            if b - first[cid] >= 100 * 1024:
                active.add(cid)
            if cid in prev and cid in active:     # need a previous reading for a rate
                kbps = (b - prev[cid][0]) / 1024.0 / dt
                rows.append([datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                             cid, f"{kbps:.0f}", f"{rtt:.1f}", ret, un, peer_ip])
        if rows:
            rotate(path)
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["ts", "conn", "recv_kBps", "rtt_ms", "retrans_total", "unacked", "peer_ip"])
                w.writerows(rows)
        prev, prev_t = cur, now
        for gone in set(first) - set(cur):
            first.pop(gone, None); active.discard(gone)
        time.sleep(max(0.0, 1.0 - (time.time() - now)))


def judge(k, win=30, frac=0.5, quiet=50, longgap=15):
    """k = kB received per second. The feed is BURSTY and bitrates differ per channel, so each session is
    judged against its OWN average: a second is 'slow' when the trailing `win`-second average is under
    `frac` of the session mean (needs 2*win seconds of data). Also: longest silent gap (< `quiet` kB/s)
    and how many gaps exceeded `longgap` s (normal burst spacing is ~7 s).
    -> (slow_seconds, longest_slow_run, longest_gap_s, gaps_over_longgap)"""
    mean = sum(k) / len(k) if k else 0
    slow = []
    for i in range(len(k)):
        w = k[max(0, i - win + 1):i + 1]
        slow.append(len(k) >= 2 * win and len(w) == win and sum(w) / win < frac * mean)
    best = cur = 0
    for f in slow:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    gaps, run = [], 0
    for x in k + [10 ** 9]:
        if x < quiet:
            run += 1
        else:
            if run: gaps.append(run)
            run = 0
    return sum(slow), best, max(gaps, default=0), sum(1 for g in gaps if g > longgap)


def summary():
    path = os.path.join(LOGDIR, "tap.csv")
    if not os.path.exists(path):
        print("no data yet"); return
    by = {}
    for r in csv.DictReader(open(path)):
        by.setdefault(r["conn"], []).append(r)
    print(f"{'conn':7} {'start':19} {'secs':>6} {'slow_s':>6} {'longest':>7} {'gap_s':>5} {'gaps>15s':>8} {'mean_kBps':>9} {'retrans':>7}  peer")
    for cid, rs in by.items():
        if len(rs) < 30:
            continue                              # guide/playlist fetches and test blips
        k = [float(r["recv_kBps"]) for r in rs]
        slow, longest, gap, longgaps = judge(k)
        print(f"{cid:7} {rs[0]['ts']:19} {len(rs):6d} {slow:6d} {longest:7d} {gap:5d} {longgaps:8d} {sum(k)/len(k):9.0f} "
              f"{int(rs[-1]['retrans_total']) - int(rs[0]['retrans_total']):7d}  {','.join(sorted({r['peer_ip'] for r in rs}))}")


if __name__ == "__main__":
    summary() if "--summary" in sys.argv else run()
