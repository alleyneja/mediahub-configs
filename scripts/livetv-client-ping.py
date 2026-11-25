#!/usr/bin/env python3
"""Passive Wi-Fi quality log for the Amazon (Fire TV) devices on the LAN, pinged from r9 (#87, #63).

Why: on Nov 23 (MNF) the feed and r9 were fine and Jay's Fire Stick (Wi-Fi, ~30 ft from the router) had repeated stalls while
a second viewer on the same feed did not. Fire TVs expose no signal data over the network (ADB is off), so this measures what we can
from r9: round-trip time and loss to each device, every PING_EVERY seconds. A sleeping device does not answer; only changes are
logged while a device is unreachable, so an idle night costs a few rows. Correlate with ~/logs/livetv-viewer-log/viewers.csv.

Log: ~/logs/livetv-client-ping/ping.csv  columns: ts, ip, rtt_ms (blank = no answer)
Devices: AdGuard query log clients that asked for Amazon device domains (found 2026-11-25); edit DEVICES if the DHCP leases change.
"""
import os, subprocess, time, re, threading
from datetime import datetime

DEVICES = os.environ.get("PING_DEVICES", "192.168.0.178,192.168.0.138,192.168.0.219,192.168.0.157").split(",")
PING_EVERY = int(os.environ.get("PING_EVERY", "5"))
OUT = os.path.expanduser("~/logs/livetv-client-ping/ping.csv")
MAX_BYTES = 40 * 1024 * 1024
RTT = re.compile(r"time[=<]([\d.]+)")


def ping(ip):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip], capture_output=True, text=True, timeout=3)
        m = RTT.search(r.stdout)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if not os.path.exists(OUT):
        open(OUT, "w").write("ts,ip,rtt_ms\n")
    lost = {ip: False for ip in DEVICES}
    while True:
        t0, now = time.time(), datetime.now().isoformat(timespec="seconds")
        res = {}
        ths = [threading.Thread(target=lambda ip=ip: res.__setitem__(ip, ping(ip))) for ip in DEVICES]
        [t.start() for t in ths]; [t.join() for t in ths]
        rows = []
        for ip in DEVICES:
            rtt = res.get(ip)
            if rtt is None:
                if not lost[ip]:
                    rows.append("%s,%s," % (now, ip))                 # first miss after being reachable: record, then stay quiet
                lost[ip] = True
            else:
                lost[ip] = False
                rows.append("%s,%s,%.1f" % (now, ip, rtt))
        if rows:
            with open(OUT, "a") as f:
                f.write("\n".join(rows) + "\n")
        try:
            if os.path.getsize(OUT) > MAX_BYTES:
                os.replace(OUT, OUT + ".1"); open(OUT, "w").write("ts,ip,rtt_ms\n")
        except OSError:
            pass
        time.sleep(max(0.5, PING_EVERY - (time.time() - t0)))


if __name__ == "__main__":
    main()
