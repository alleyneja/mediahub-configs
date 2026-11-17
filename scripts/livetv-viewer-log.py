#!/usr/bin/env python3
"""livetv-viewer-log.py - passive record of WHO is watching and on WHAT device (#87).

Run on r9. Polls Plex's own /status/sessions (local API) every POLL seconds and writes one row per
active session. Opens no provider connection and uses no provider slot. Plex keeps no history of
Live TV viewers and its detailed logs rotate in under an hour, so without this a "Playback error"
report cannot be tied to an account, device or channel afterwards.

Log: ~/logs/livetv-viewer-log/viewers.csv
  ts,session,live,user,device,product,platform,address,remote,state,title,channel,decision,transcode_kbps
  (also rows with session='-' and user='API-NO-ANSWER' when Plex stops answering, with the error)
The Plex token is read from Preferences.xml at runtime and never logged. Rotates at 50 MB.
Env: VIEWER_LOGDIR, VIEWER_POLL (seconds, default 10).
Usage: livetv-viewer-log.py            run forever (systemd)
       livetv-viewer-log.py --summary  who watched what, per session, from the log
"""
import csv, glob, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime

LOGDIR = os.environ.get("VIEWER_LOGDIR", os.path.expanduser("~/logs/livetv-viewer-log"))
CSV = os.path.join(LOGDIR, "viewers.csv")
POLL = int(os.environ.get("VIEWER_POLL", "10"))
BASE = "http://127.0.0.1:32400"
COLS = ["ts", "session", "live", "user", "device", "product", "platform", "address", "remote", "state",
        "title", "channel", "decision", "transcode_kbps"]


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
            (m.get("title") or "")[:40], chan[:30], dec, int(t.get("bitrate") or 0) if t else 0]


def write(rows):
    if not rows:
        return
    os.makedirs(LOGDIR, exist_ok=True)
    if os.path.exists(CSV) and os.path.getsize(CSV) > 50 * 1024 * 1024:
        os.replace(CSV, CSV + ".1")
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        w.writerows(rows)


def run():
    tok, down = None, False
    while True:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            tok = tok or token()
            rows = [row(m, now) for m in fetch(tok)]
            down = False
        except urllib.error.HTTPError as e:
            tok = None                                    # token may have been rewritten; re-read next time
            rows = [[now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", "http %s" % e.code, "", "", ""]]
        except Exception as e:
            rows = [[now, "-", "-", "API-NO-ANSWER", "-", "-", "-", "-", "-", "-", str(e)[:40], "", "", ""]] if not down else []
            down = True                                   # log the first failure of an outage, not every poll
        write(rows)
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
