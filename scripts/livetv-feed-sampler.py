#!/usr/bin/env python3
"""Live TV feed sampler (alleyneja/mediahub-issues#87).

Every run (cron, every 10 min) measures the IPTV provider's WESH feed three ways and appends one row to a CSV:
  direct TS  - raw MPEG-TS from this host's normal internet path (what Plex/Threadfin use today)
  direct HLS - the same station as ~10 s HLS segments (candidate fix)
  via VPN    - raw TS fetched from inside gluetun's network namespace (candidate fix)

Safety:
  * Skips the whole cycle if the provider reports any active connection (never takes a slot from a viewer),
    and re-checks between routes. The provider allows 2; this holds at most 1.
  * Self-expires (default 7 days after first run), STOP file as a kill switch, flock against overlap.
  * No credentials in this file or the log: the stream URL is read at runtime from Threadfin's playlist.

Usage:  livetv-feed-sampler.py            take one sample (cron)
        livetv-feed-sampler.py --summary  print a report of what has been logged
Kill:   touch ~/logs/livetv-sampler/STOP    (or remove the crontab line)
"""
import csv, fcntl, json, os, re, signal, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta

DIR = os.path.expanduser("~/logs/livetv-sampler")
CSV = os.path.join(DIR, "samples.csv")
STOP = os.path.join(DIR, "STOP")
START = os.path.join(DIR, "started")
M3U = "/srv/docker/threadfin/conf/data/MLXG7IZI17KCJ5IPBDCN.m3u"
TVG_ID = "nbcwesh.us"            # channel 1021, USA NBC 2 WESH Orlando
RUN_DAYS = 7
SAMPLE_SECS = 15                 # data-reading window per route
STARVED_KBPS = 1000              # the stream needs ~1000 KB/s; below this the player drains
COLS = ["ts", "skipped", "ts_kbps", "ts_ttfb", "ts_maxgap", "ts_cdn",
        "hls_kbps", "hls_worst_ratio", "hls_cdn", "vpn_kbps", "vpn_ttfb", "vpn_maxgap", "vpn_cdn", "notes"]

# Identical measuring code runs on the host and inside gluetun's namespace (docker exec into qbittorrent).
MEASURE = r'''
import os, sys, time, json, urllib.request
url, secs = os.environ["SAMPLER_URL"], float(os.environ["SAMPLER_SECS"])
out = {}
try:
    t0 = time.time(); r = urllib.request.urlopen(url, timeout=15); out["ttfb"] = round(time.time() - t0, 2)
    out["cdn"] = r.geturl().split("/")[2].split(".")[0]
    n = 0; last = time.time(); maxgap = 0.0; start = time.time()
    while time.time() - start < secs:
        d = r.read(65536)
        if not d: break
        now = time.time(); maxgap = max(maxgap, now - last); last = now; n += len(d)
    el = max(time.time() - start, 0.001)
    # a feed that stops sending counts the silent tail as a gap too
    maxgap = max(maxgap, time.time() - last)
    out.update(kbps=int(n / 1024 / el), maxgap=round(maxgap, 1))
except Exception as e:
    out["error"] = str(e)[:80]
print(json.dumps(out))
'''

def stream_base():
    """Return (playlist_entry_url, api_base) for WESH from Threadfin's playlist; never logged."""
    t = open(M3U, errors="ignore").read().split("\n")
    for i, l in enumerate(t):
        if l.startswith("#EXTINF") and 'tvg-id="%s"' % TVG_ID in l:
            url = t[i + 1].strip()
            m = re.match(r"(https?://[^/]+)/([^/]+)/([^/]+)/(\d+)\.(ts|m3u8)$", url)
            if m:
                host, user, pw, sid, _ = m.groups()
                return host, user, pw, sid
    raise RuntimeError("WESH not found in playlist")

def active_conns(host, user, pw):
    d = json.load(urllib.request.urlopen("%s/player_api.php?username=%s&password=%s" % (host, user, pw), timeout=15))
    return int(d["user_info"]["active_cons"])

def run_measure(url, via_vpn):
    env = {"SAMPLER_URL": url, "SAMPLER_SECS": str(SAMPLE_SECS)}
    if via_vpn:
        cmd = ["docker", "exec", "-i", "-e", "SAMPLER_URL", "-e", "SAMPLER_SECS", "qbittorrent", "python3", "-c", MEASURE]
    else:
        cmd = [sys.executable, "-I", "-c", MEASURE]
    r = subprocess.run(cmd, env={**os.environ, **env}, capture_output=True, text=True, timeout=SAMPLE_SECS + 45)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": "no output: " + r.stderr.strip()[-60:]}

def measure_hls(host, user, pw, sid):
    """Download 2 recent HLS segments and time them to the LAST BYTE. The CDN does not always close the connection
    after a segment (curl showed an extra ~15 s wait), so a short idle gap after data counts as 'finished'.
    ratio = download seconds / media seconds; >= 1 means the player would fall behind real time."""
    import http.client, socket
    base = "%s/live/%s/%s/%s.m3u8" % (host, user, pw, sid)
    r = urllib.request.urlopen(base, timeout=15)
    final = r.geturl(); cdn = final.split("/")[2].split(".")[0]; chost = final.split("/")[2]
    pl = r.read().decode().split("\n")
    segs = [(float(l[8:].split(",")[0]), pl[i + 1].strip()) for i, l in enumerate(pl) if l.startswith("#EXTINF")][-3:-1]
    tot = nbytes = 0; worst = 0.0
    for dur, p in segs:
        path = p if p.startswith("/") else "/" + p.split("/", 3)[-1]
        s0 = time.time(); c = http.client.HTTPConnection(chost, timeout=15)
        c.request("GET", path); resp = c.getresponse(); c.sock.settimeout(2.5)
        got = 0; last = time.time()
        while time.time() - s0 < 30:
            try: d = resp.read(65536)
            except socket.timeout: break               # idle after data = segment complete (server holding socket open)
            if not d: break
            got += len(d); last = time.time()
        c.close(); el = max(last - s0, 0.001) if got else time.time() - s0
        tot += el; nbytes += got; worst = max(worst, el / dur)
    return {"kbps": int(nbytes / 1024 / max(tot, 0.001)), "worst_ratio": round(worst, 2), "cdn": cdn}

def sample():
    os.makedirs(DIR, exist_ok=True)
    lock = open(os.path.join(DIR, "lock"), "w")
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return
    if os.path.exists(STOP): return
    if not os.path.exists(START): open(START, "w").write(datetime.now().isoformat())
    if datetime.now() > datetime.fromisoformat(open(START).read().strip()) + timedelta(days=RUN_DAYS): return
    def _wd(*_): raise TimeoutError("watchdog: cycle exceeded 240 s")
    signal.signal(signal.SIGALRM, _wd); signal.alarm(240)   # a cron cycle can never hang
    row = {c: "" for c in COLS}; row["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    notes = []
    try:
        host, user, pw, sid = stream_base()
        def busy(settle=True):
            # The provider keeps counting our own just-closed connection for ~5 s (measured 2026-11-08), so give the
            # count up to 20 s to drop to 0 before deciding a real viewer is present.
            for i in range(7):
                if active_conns(host, user, pw) == 0: return False
                if not settle or i == 6: break
                time.sleep(3)
            return True
        if busy(settle=False):
            row["skipped"] = "viewer_active"
        else:
            ts_url = "%s/%s/%s/%s.ts" % (host, user, pw, sid)
            m = run_measure(ts_url, False)
            row.update(ts_kbps=m.get("kbps", ""), ts_ttfb=m.get("ttfb", ""), ts_maxgap=m.get("maxgap", ""), ts_cdn=m.get("cdn", ""))
            if "error" in m: notes.append("ts:" + m["error"])
            if busy(): row["skipped"] = "viewer_started_after_ts"
            else:
                try:
                    h = measure_hls(host, user, pw, sid)
                    row.update(hls_kbps=h["kbps"], hls_worst_ratio=h["worst_ratio"], hls_cdn=h["cdn"])
                except Exception as e: notes.append("hls:" + str(e)[:60])
                if busy(): row["skipped"] = "viewer_started_after_hls"
                else:
                    v = run_measure(ts_url, True)
                    row.update(vpn_kbps=v.get("kbps", ""), vpn_ttfb=v.get("ttfb", ""), vpn_maxgap=v.get("maxgap", ""), vpn_cdn=v.get("cdn", ""))
                    if "error" in v: notes.append("vpn:" + v["error"])
    except Exception as e:
        notes.append("fatal:" + str(e)[:80])
    row["notes"] = ";".join(notes)
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.DictWriter(f, COLS)
        if new: w.writeheader()
        w.writerow(row)

def summary():
    rows = list(csv.DictReader(open(CSV)))
    if not rows: print("no samples yet"); return
    def f(r, k):
        try: return float(r[k])
        except (ValueError, KeyError): return None
    done = [r for r in rows if not r["skipped"]]
    print("samples: %d (%d measured, %d skipped for viewers, %d with errors)  %s -> %s" % (
        len(rows), len(done), len(rows) - len(done), sum(1 for r in rows if r["notes"]), rows[0]["ts"], rows[-1]["ts"]))
    names = [("direct TS", "ts_kbps"), ("direct HLS", "hls_kbps"), ("via VPN", "vpn_kbps")]
    print("\n%-11s %5s %8s %8s %12s" % ("route", "n", "median", "min", "starved(<%d)" % STARVED_KBPS))
    for name, k in names:
        v = sorted(x for x in (f(r, k) for r in done) if x is not None)
        if v: print("%-11s %5d %8d %8d %6d (%2d%%)" % (name, len(v), v[len(v) // 2], v[0], sum(1 for x in v if x < STARVED_KBPS), 100 * sum(1 for x in v if x < STARVED_KBPS) // len(v)))
    hv = [x for x in (f(r, "hls_worst_ratio") for r in done) if x is not None]
    if hv: print("\nHLS cycles falling behind real time (worst segment ratio >= 1.0): %d of %d" % (sum(1 for x in hv if x >= 1.0), len(hv)))
    print("\nBad windows (direct TS starved) and what the other routes did:")
    bad = [r for r in done if (f(r, "ts_kbps") is not None and f(r, "ts_kbps") < STARVED_KBPS)]
    for r in bad[-25:]:
        print("  %s  TS %5s KB/s (gap %ss, cdn %s) | HLS %s KB/s ratio %s | VPN %s KB/s (gap %ss)" % (
            r["ts"], r["ts_kbps"], r["ts_maxgap"], r["ts_cdn"], r["hls_kbps"] or "-", r["hls_worst_ratio"] or "-", r["vpn_kbps"] or "-", r["vpn_maxgap"] or "-"))
    if not bad: print("  none yet")
    byh = {}
    for r in done:
        k = f(r, "ts_kbps")
        if k is not None: byh.setdefault(r["ts"][11:13], []).append(k < STARVED_KBPS)
    print("\nStarved share of direct-TS samples by hour:")
    print("  " + "  ".join("%sh:%d%%" % (h, 100 * sum(v) // len(v)) for h, v in sorted(byh.items())))

if __name__ == "__main__":
    summary() if "--summary" in sys.argv else sample()
