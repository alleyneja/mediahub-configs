#!/usr/bin/env python3
"""
Watch Nextcloud saves and capture what actually goes wrong.

Two surfaces, because they fail differently:
  1. Apache access log (docker logs) -- every DAV request + HTTP status.
     Catches failures that never write an exception.
  2. nextcloud.log -- the exception class and message, which is the
     thing that distinguishes the four bugs that share one banner.

On any failure it snapshots context that is only true at that moment:
which mergerfs branch the target sits on, the chunked-upload staging
dir, NFS counters, and kernel NFS complaints.

Run:  ./nextcloud-save-watch.py
Log:  ~/logs/nextcloud-save-watch.log
"""
import json, os, re, subprocess, sys, threading, time
from datetime import datetime

USER = "authentik-bd547cab3cf4f36c00e4fe8d4e99387b"
LOG = os.path.expanduser("~/logs/nextcloud-save-watch.log")
BRANCHES = ["/mnt/internal", "/mnt/nas"]
DATA = "nextcloud/data/%s" % USER

ACCESS = re.compile(r'"(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<status>\d{3}) (?P<size>\S+)')
WRITE_METHODS = {"PUT", "MOVE", "POST", "DELETE", "MKCOL", "COPY"}
lock = threading.Lock()


def out(s):
    with lock:
        print(s, flush=True)
        with open(LOG, "a") as f:
            f.write(s + "\n")


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception as e:
        return "<%s>" % e


def branch_of(rel):
    """Which mergerfs branch physically holds this path under the user's data dir?"""
    hits = []
    for b in BRANCHES:
        p = os.path.join(b, DATA, rel.lstrip("/"))
        if sh('sudo test -e "%s" && echo yes' % p.replace('"', '\\"')) == "yes":
            hits.append(os.path.basename(b))
    return "+".join(hits) if hits else "(absent on both)"


def snapshot(why, detail):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out("\n" + "=" * 78)
    out("FAILURE  %s   %s" % (ts, why))
    out("=" * 78)
    for line in detail.splitlines():
        out("  " + line)

    # Which branch holds the destination, and its parent chain?
    path = detail_path(detail)
    if path:
        out("\n  -- mergerfs branch resolution --")
        parts = [p for p in path.split("/") if p]
        acc = ""
        for p in parts:
            acc = acc + "/" + p
            out("     %-58s %s" % (acc[:58], branch_of("files" + acc)))

    out("\n  -- chunked-upload staging --")
    for b in BRANCHES:
        u = os.path.join(b, DATA, "uploads")
        n = sh('sudo ls -1 "%s" 2>/dev/null | wc -l' % u)
        out("     %-14s sessions=%s" % (b, n))
    out("     detail: " + (sh('sudo ls -la %s/%s/uploads 2>/dev/null | tail -n +4'
                              % (BRANCHES[1], DATA)) or "(empty)").replace("\n", "\n             "))

    out("\n  -- NFS --")
    out("     retrans: " + (sh("nfsstat -c 2>/dev/null | awk '/^[0-9]/{print $1\" calls \"$2\" retrans\"; exit}'") or "?"))
    dm = sh("sudo dmesg -T 2>/dev/null | grep -iE 'nfs.*(not responding|timed out|error)' | tail -3")
    out("     dmesg  : " + (dm.replace("\n", "\n              ") if dm else "(no NFS complaints)"))

    out("\n  -- space --")
    out("     " + sh("df -h /mnt/internal /mnt/nas 2>/dev/null | tail -2").replace("\n", "\n     "))
    out("=" * 78 + "\n")


def detail_path(detail):
    m = re.search(r"path=(\S+)", detail)
    if not m:
        return None
    p = m.group(1)
    p = re.sub(r"^/remote\.php/dav/files/[^/]+", "", p)
    if p.startswith("/remote.php") or not p.startswith("/"):
        return None
    import urllib.parse
    return urllib.parse.unquote(p)


def watch_access():
    p = subprocess.Popen(["docker", "logs", "-f", "--tail", "0", "nextcloud"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        m = ACCESS.search(line)
        if not m:
            continue
        meth, path, status = m.group("method"), m.group("path"), int(m.group("status"))
        if "/remote.php/dav" not in path:
            continue
        if meth in WRITE_METHODS:
            if status >= 400:
                snapshot("HTTP %d on %s" % (status, meth),
                         "method=%s\nstatus=%d\npath=%s" % (meth, status, path))
            else:
                out("  ok  %-6s %3d  %s" % (meth, status, path[:100]))


def watch_nclog():
    p = subprocess.Popen(
        ["docker", "exec", "nextcloud", "tail", "-F", "-n", "0",
         "/var/www/html/data/nextcloud.log"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        try:
            d = json.loads(line)
        except Exception:
            continue
        ex = d.get("exception") or {}
        cls = ex.get("Exception", "")
        if not cls:
            continue
        trace = ex.get("Trace") or []
        frames = []
        for fr in trace[:14]:
            if isinstance(fr, dict):
                frames.append("       %s%s%s()  @ %s:%s" % (
                    fr.get("class", ""), fr.get("type", ""), fr.get("function", ""),
                    str(fr.get("file", "?")).replace("/var/www/html/", ""), fr.get("line", "?")))
            else:
                frames.append("       %s" % fr)
        snapshot("EXCEPTION %s" % cls.split("\\")[-1],
                 "class=%s\nmessage=%s\nmethod=%s\npath=%s\nuser=%s\nthrown_at=%s:%s\ntrace=\n%s"
                 % (cls, str(ex.get("Message"))[:400], d.get("method"),
                    d.get("url"), d.get("user"),
                    str(ex.get("File", "?")).replace("/var/www/html/", ""), ex.get("Line", "?"),
                    "\n".join(frames) or "       (none)"))


if __name__ == "__main__":
    out("\n### watcher started %s -- try your save now"
        % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    out("### watching: apache access log (DAV writes) + nextcloud.log exceptions")
    for fn in (watch_access, watch_nclog):
        threading.Thread(target=fn, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        out("### watcher stopped")
