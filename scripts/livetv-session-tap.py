#!/usr/bin/env python3
"""livetv-session-tap.py - passive per-second log of REAL Live TV streams (#87).

Run on r9 (where Plex lives). Threadfin runs with buffer off and hands Plex the provider
URL, so Plex's own TCP connections to the IPTV provider ARE the viewing sessions. This
reads kernel TCP counters via `ss -tin` once a second; it opens no connection of its own,
so it never uses one of the account's 2 slots and it covers 100% of viewing time.

Writes one CSV row per connection per second, only while a stream exists (idle = no rows):
  ts,conn,recv_kBps,rtt_ms,retrans_total,unacked,state_flags
Never logs URLs, credentials or remote ports beyond a short connection hash.

Stall rule used by --summary: the feed is BURSTY (several MB every 6-8 s, near-silence between),
so per-second rates are meaningless. A second is "starved" when the rolling 10 s average is under
1000 kB/s (what the stream needs); also reports the longest silent gap.

Usage:  livetv-session-tap.py            run forever (systemd)
        livetv-session-tap.py --summary  print stalls per session from the log
"""
import csv, os, re, socket, subprocess, sys, time, hashlib
from datetime import datetime

HOST = os.environ.get("TAP_PROVIDER_HOST", "link4tv.cc")
LOGDIR = os.environ.get("TAP_LOGDIR", os.path.expanduser("~/logs/livetv-session-tap"))
MAX_BYTES = 200 * 1024 * 1024          # rotate at 200 MB, keep 1 old file
STOP = os.path.join(LOGDIR, "STOP")


def provider_ips():
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(HOST, 80, socket.AF_INET)})
    except OSError:
        return []


def snapshot(ips):
    """Return {conn_id: (bytes_received, rtt_ms, retrans_total, unacked)} for provider conns."""
    out = {}
    for ip in ips:
        r = subprocess.run(["ss", "-tinH", "state", "established", "dst", ip],
                           capture_output=True, text=True)
        lines = r.stdout.splitlines()
        # ss prints the socket line, then an indented info line
        for i in range(0, len(lines) - 1, 2):
            head, info = lines[i].split(), lines[i + 1]
            if len(head) < 4:
                continue
            local, peer = head[2], head[3]
            cid = hashlib.sha1((local + peer).encode()).hexdigest()[:6]
            m = re.search(r"bytes_received:(\d+)", info)
            rtt = re.search(r"rtt:([\d.]+)/", info)
            ret = re.search(r"retrans:\d+/(\d+)", info)
            un = re.search(r"unacked:(\d+)", info)
            if not m:
                continue
            out[cid] = (int(m.group(1)), float(rtt.group(1)) if rtt else -1.0,
                        int(ret.group(1)) if ret else 0, int(un.group(1)) if un else 0)
    return out


def rotate(path):
    if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
        os.replace(path, path + ".1")


def run():
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, "tap.csv")
    ips, ips_at, prev, prev_t = provider_ips(), time.time(), {}, time.time()
    first, active = {}, set()     # a conn is a "stream" once it has moved >=100 kB (skips idle keepalives)
    while not os.path.exists(STOP):
        if time.time() - ips_at > 300:           # CDN address can change
            ips, ips_at = provider_ips() or ips, time.time()
        now = time.time()
        cur = snapshot(ips)
        dt = max(now - prev_t, 0.001)
        rows = []
        for cid, (b, rtt, ret, un) in cur.items():
            first.setdefault(cid, b)
            if b - first[cid] >= 100 * 1024:
                active.add(cid)
            if cid in prev and cid in active:     # need a previous reading for a rate
                kbps = (b - prev[cid][0]) / 1024.0 / dt
                rows.append([datetime.fromtimestamp(now).isoformat(timespec="seconds"),
                             cid, f"{kbps:.0f}", f"{rtt:.1f}", ret, un])
        if rows:
            rotate(path)
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["ts", "conn", "recv_kBps", "rtt_ms", "retrans_total", "unacked"])
                w.writerows(rows)
        prev, prev_t = cur, now
        for gone in set(first) - set(cur):
            first.pop(gone, None); active.discard(gone)
        time.sleep(max(0.0, 1.0 - (time.time() - now)))


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
    path = os.path.join(LOGDIR, "tap.csv")
    if not os.path.exists(path):
        print("no data yet"); return
    by = {}
    for r in csv.DictReader(open(path)):
        by.setdefault(r["conn"], []).append(r)
    print(f"{'conn':7} {'start':19} {'secs':>6} {'starved_s':>9} {'longest':>7} {'gap_s':>5} {'mean_kBps':>9} {'retrans':>7}")
    for cid, rs in by.items():
        k = [float(r["recv_kBps"]) for r in rs]
        starved, longest, gap = judge(k)
        print(f"{cid:7} {rs[0]['ts']:19} {len(rs):6d} {starved:9d} {longest:7d} {gap:5d} {sum(k)/len(k):9.0f} "
              f"{int(rs[-1]['retrans_total']) - int(rs[0]['retrans_total']):7d}")


if __name__ == "__main__":
    summary() if "--summary" in sys.argv else run()
