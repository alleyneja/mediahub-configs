#!/usr/bin/env python3
"""uplink-profile.py - time-of-day profile of the home UPLOAD capacity (#87).

Run on prod (it has the speedtest tool; same internet line as r9) from a systemd timer (every 45 min). Plex streams to remote viewers use the uplink, and Jay's
line capacity moved between 1.3 and 35 Mbps in the few measurements we have. This records one upload-only
speedtest per run so Sunday-evening capacity can be compared with a quiet weekday.
Skips (and logs the skip) while any REMOTE viewer is active, so it never competes with a stream.
Log: ~/logs/uplink-profile/uplink.csv   ts,status,upload_mbps,ping_ms,server
"""
import csv, json, os, subprocess, sys
from datetime import datetime, timedelta

LOGDIR = os.environ.get("UPLINK_LOGDIR", os.path.expanduser("~/logs/uplink-profile"))
CSV = os.path.join(LOGDIR, "uplink.csv")
VIEWERS = os.path.expanduser("~/logs/livetv-viewer-log/viewers.csv")
VIEWER_HOST = os.environ.get("UPLINK_VIEWER_HOST", "")      # set to r9's address when the viewer log lives on another machine


def remote_active():
    """True if any remote session was logged in the last 90 s (viewer log column 'remote' == 1)."""
    try:
        cut = (datetime.now() - timedelta(seconds=90)).isoformat(timespec="seconds")
        if VIEWER_HOST:
            txt = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", VIEWER_HOST, "tail -n 60 ~/logs/livetv-viewer-log/viewers.csv"],
                                 capture_output=True, text=True, timeout=15).stdout
            hdr = ["ts", "session", "live", "user", "device", "product", "platform", "address", "remote"]
            rows = [dict(zip(hdr, l.split(","))) for l in txt.splitlines() if l and not l.startswith("ts,")]
        else:
            rows = list(csv.DictReader(open(VIEWERS)))
        return any(r["ts"] >= cut and r.get("remote") == "1" for r in rows)
    except Exception:
        return True                      # can't tell whether someone is streaming: don't risk a test


def main():
    os.makedirs(LOGDIR, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    row = [now, "", "", "", ""]
    if remote_active():
        row[1] = "skipped-remote-viewer"
    else:
        try:
            out = subprocess.run(["speedtest", "--no-download", "--json"], capture_output=True, text=True, timeout=90).stdout
            d = json.loads(out)
            row[1:] = ["ok", "%.1f" % (d["upload"] / 1e6), "%.0f" % d["ping"], (d["server"]["sponsor"] + " " + d["server"]["name"])[:40]]
        except Exception as e:
            row[1] = "error " + str(e)[:40]
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "status", "upload_mbps", "ping_ms", "server"])
        w.writerow(row)
    print(",".join(row))


if __name__ == "__main__":
    main()
