#!/usr/bin/env python3
"""Weekly plain-English digest of repeating container errors (issue #71).

Asks the central Loki (stacks/loki-r9) which containers printed the same error over and over this week,
compares with last week's digest (state file), and posts one Discord message to a muted channel.
When something NEW shows up it also opens/updates ONE rolling GitHub issue in the private issues repo.
Read-only against Loki and the containers; never restarts anything.

  log-digest.py --dry-run          print the digest, change nothing (no Discord, no GitHub, no state)
  log-digest.py                    real run (cron: Sunday 09:00 on production)
  log-digest.py --freshness        exit 1 and print a line if a host has sent no logs for 1h (no digest)

Config: scripts/log-digest.env (gitignored): DISCORD_WEBHOOK_URL=, optional LOKI_URL=.
Noise control: scripts/log-digest-ignore.txt, one regex per line, matched against "<container>: <normalized message>".
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from subprocess import run

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "log-digest.env")
IGNORE_FILE = os.path.join(HERE, "log-digest-ignore.txt")
STATE_DIR = os.path.expanduser("~/logs/log-digest")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
ISSUES_REPO = "alleyneja/mediahub-issues"
ISSUE_TITLE = "Log digest: new repeating container errors"
HOSTS = ["i7", "r9"]          # x51 joins when it has a collector
WINDOW_DAYS = 7
MIN_COUNT = 20                # a message is "repeating" at >= this many in the window ...
MIN_DAYS = 3                  # ... or on >= this many different days (with >= 5 occurrences)
# Plain substring filters (case variants spelled out) are ~25x faster in Loki than one (?i) regex: 1 s vs 30 s per day.
ERRORISH = ["rror", "ERROR", "ailed", "ailure", "FAIL", "efused", "REFUSED", "nreachable", "UNREACHABLE", "imeout", "imed out",
            "TIMEOUT", "ECONN", "ENOTFOUND", "EAI_AGAIN", "atal", "FATAL", "xception", "EXCEPTION", "raceback", "anic", "enied", "DENIED"]

LOW_LEVEL = re.compile(r"(\[(info|debug|trace)\]|level=(info|debug)|\b(INFO|DEBUG|TRACE)\b)", re.I)
STRONG = re.compile(r"ECONNREFUSED|ENETUNREACH|EHOSTUNREACH|ENOTFOUND|EAI_AGAIN|ETIMEDOUT|No route to host|Connection refused|Traceback|panic|FATAL|Fatal|Unauthorized|401|403|disk full|No space left")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LEAD_TS = re.compile(r"^\s*(\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?|\w{3},? \w{3} \d+ \d{2}:\d{2}:\d{2}(?:\.\d+)?|\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*")
IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HEX = re.compile(r"\b(?:0x)?[0-9a-f]{8,}\b", re.I)


def load_env():
    env = {}
    try:
        for line in open(ENV_FILE):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def loki_get(base, path, params):
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


TIMESTAMP = re.compile(r"\[?\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?|\b\d{1,2}[:.]\d{2}:\d{2}(?:\.\d+)?\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
UNITS = {"ms", "us", "ns", "s", "m", "h", "d", "b", "kb", "mb", "gb", "tb", "kib", "mib", "gib", "tib", "mbps", "kbps"}
TOKEN = re.compile(r"\b\d+([A-Za-z%]*)\b")


def _num(m):
    suffix = m.group(1)
    if suffix and suffix.lower() not in UNITS:
        return m.group(0)                      # 4K, 1080p, 5xx: the digits are part of a word, keep them
    return "N" + suffix


SECRET = re.compile(r"(?i)((?:api[-_]?key|apikey|x-plex-token|token|passw(?:or)?d|passwd|secret|authorization|bearer|auth|key)[\"']?\s*[=:]\s*[\"']?(?:bearer\s+)?)[^\s&\"',}]{4,}")


def redact(text):
    """Credentials in log lines (apikey=..., X-Plex-Token=..., password: ...) must never reach Discord or GitHub."""
    return SECRET.sub(lambda m: m.group(1) + "<redacted>", text)


def strip_time(line):
    return redact(TIMESTAMP.sub("<time>", ANSI.sub("", line.replace("\x00", "")).strip()))


def normalize(line):
    """Make 'the same error' compare equal: drop colors/timestamps/ids; IPs and digits inside words (4K, h264) are kept."""
    s = re.sub(r"^(<time>\s*)+", "", LEAD_TS.sub("", strip_time(line)))
    s = UUID.sub("<id>", s)
    s = HEX.sub("<hex>", s)
    parts = re.split(r"(\b\d{1,3}(?:\.\d{1,3}){3}\b)", s)       # odd parts are IP addresses: leave untouched
    s = "".join(p if i % 2 else TOKEN.sub(_num, p) for i, p in enumerate(parts))
    s = re.sub(r"\s+", " ", s)
    return s[:170]


def fetch_errors(base, start_ns, end_ns):
    """Yield (container, host, ts_ns, line) for error-ish lines, paging through Loki."""
    q = '{host=~".+"} |= ' + " or ".join(json.dumps(t) for t in ERRORISH)
    cur = start_ns
    while True:
        d = loki_get(base, "/loki/api/v1/query_range", {
            "query": q, "start": cur, "end": end_ns, "limit": 5000, "direction": "forward"})
        n, last = 0, cur
        for s in d["data"]["result"]:
            c, h = s["stream"].get("container", "?"), s["stream"].get("host", "?")
            for ts, line in s["values"]:
                n += 1
                last = max(last, int(ts))
                yield c, h, int(ts), line
        if n < 5000 or last <= cur:
            return
        cur = last + 1


def last_seen_per_host(base):
    out = {}
    for h in HOSTS:
        d = loki_get(base, "/loki/api/v1/query_range", {
            "query": '{host="%s"}' % h, "limit": 1, "direction": "backward",
            "start": int((time.time() - 3 * 86400) * 1e9), "end": int(time.time() * 1e9)})
        vals = [int(v[0]) for s in d["data"]["result"] for v in s["values"]]
        out[h] = max(vals) / 1e9 if vals else None
    return out


def ago(sec):
    if sec < 5400: return f"{int(sec/60)} min"
    if sec < 172800: return f"{int(sec/3600)} h"
    return f"{int(sec/86400)} days"


def fmt_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%b %-d")


def nice_name(container):
    return container if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27}", container) else "minecraft-server " + container[:8]


def build(base):
    end = time.time()
    start = end - WINDOW_DAYS * 86400
    ignores = []
    if os.path.exists(IGNORE_FILE):
        ignores = [re.compile(l.strip()) for l in open(IGNORE_FILE) if l.strip() and not l.startswith("#")]
    groups = defaultdict(lambda: {"n": 0, "days": set(), "first": None, "last": None, "sample": "", "host": "", "variants": set()})
    for c, h, ts, line in fetch_errors(base, int(start * 1e9), int(end * 1e9)):
        if LOW_LEVEL.search(line[:120]) and not STRONG.search(line):
            continue          # INFO/DEBUG chatter that merely contains a word like "failed" (e.g. "0 failed")
        sig = normalize(line)
        if not sig or any(r.search(f"{c}: {sig}") for r in ignores):
            continue
        g = groups[(c, sig)]
        t = ts / 1e9
        g["n"] += 1
        g["days"].add(datetime.fromtimestamp(t).strftime("%F"))
        g["first"] = t if g["first"] is None else min(g["first"], t)
        g["last"] = t if g["last"] is None else max(g["last"], t)
        g["host"] = h
        g["sample"] = g["sample"] or redact(ANSI.sub("", line).strip())[:230]
        if len(g["variants"]) < 5000: g["variants"].add(strip_time(line))
    rep = {}
    for key, g in groups.items():
        if g["n"] >= MIN_COUNT or (len(g["days"]) >= MIN_DAYS and g["n"] >= 5):
            rep[key] = g
    return end, rep


def sigid(c, sig):
    return f"{c}|{sig}"


def compose(end, rep, state, first_run):
    prev = state.get("signatures", {})
    now_ids = {sigid(*k): k for k in rep}
    new = [k for k in rep if sigid(*k) not in prev]
    still = [k for k in rep if sigid(*k) in prev]
    gone = [(i.split("|", 1)[0], prev[i]["sig"]) for i in prev if i not in now_ids]
    key = lambda k: -rep[k]["n"]
    lines = []
    span = f"{fmt_day(end - WINDOW_DAYS*86400)} to {fmt_day(end)}"
    lines.append(f"🗞 **Weekly container health: {span}**")
    if first_run:
        lines.append("_First digest ever: everything repeating counts as new. Next week this will be much shorter. "
                     "Tell Claude which of these are harmless and they get muted._")

    def by_container(ks):
        d = defaultdict(list)
        for k in ks: d[k[0]].append(k)
        return sorted(d.items(), key=lambda kv: -sum(rep[k]["n"] for k in kv[1]))

    def item(c, ks):
        ks = sorted(ks, key=key)
        g = rep[ks[0]]
        sig = ks[0][1]
        still_going = (end - g["last"]) < 6 * 3600
        tail = "still happening" if still_going else f"last seen {fmt_day(g['last'])}"
        more = f" (+{len(ks)-1} other kind(s))" if len(ks) > 1 else ""
        nv = len(g["variants"])
        var = f", {'5,000+' if nv >= 5000 else f'{nv:,}'} different variants (IDs/values)" if nv > 3 else ""
        return (f"• `{nice_name(c)}` ({g['host']}): \"{sig[:160]}\" — {g['n']:,}× on {len(g['days'])} day(s){var}, "
                f"since {fmt_day(g['first'])}, {tail}{more}")

    def section(title, ks, cap=10):
        if not ks: return
        lines.append(f"\n{title}")
        groups = by_container(ks)
        for c, cks in groups[:cap]: lines.append(item(c, cks))
        if len(groups) > cap:
            lines.append(f"…and {len(groups)-cap} more container(s). Full list is in the GitHub issue; or Grafana → Explore.")

    section("🆕 **New this week**", new)
    section("🔁 **Still broken from before**", still, cap=8)
    if gone:
        lines.append("\n✅ **Not seen this week (probably fixed)**")
        for c, sig in gone[:6]: lines.append(f"• `{nice_name(c)}`: \"{sig[:110]}\"")
    if not (new or still or gone):
        lines.append("\nNothing is repeating. All quiet. ✨")
    return "\n".join(lines), new, still, gone


def split_discord(text, limit=1900):
    out, cur = [], ""
    for ln in text.split("\n"):
        if len(cur) + len(ln) + 1 > limit and cur:
            out.append(cur); cur = ""
        cur += ln + "\n"
    if cur.strip(): out.append(cur)
    return out


def post_discord(webhook, text):
    for part in split_discord(text):
        req = urllib.request.Request(webhook, data=json.dumps({"content": part}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "log-digest/1"})
        urllib.request.urlopen(req, timeout=30).read()
        time.sleep(1)


def github_issue(new, rep, end, first_run):
    """Open (or comment on) ONE rolling issue; only called when there is something new."""
    body = [f"### New repeating errors, week ending {fmt_day(end)}"]
    if first_run:
        body.append("_First run: this is the baseline sweep of everything repeating in the last 7 days._")
    for k in sorted(new, key=lambda k: -rep[k]["n"])[:25]:
        g = rep[k]
        nv = len(g["variants"])
        body.append(f"- `{k[0]}` ({g['host']}): `{k[1][:170]}` — {g['n']:,}× on {len(g['days'])} day(s), since {fmt_day(g['first'])}"
                    + (f", {'5,000+' if nv >= 5000 else f'{nv:,}'} different variants" if nv > 3 else "")
                    + f"\n  - real example: `{g['sample']}`")
    body.append("\nFound by `scripts/log-digest.py` from the central Loki (docs/log-monitoring.md). "
                "Triage: fix it, or add a pattern to `scripts/log-digest-ignore.txt` if it is harmless noise.")
    text = "\n".join(body)
    found = run(["gh", "issue", "list", "-R", ISSUES_REPO, "--state", "open", "--search", f'"{ISSUE_TITLE}" in:title',
                 "--json", "number,title", "-q", f'[.[]|select(.title=="{ISSUE_TITLE}")][0].number'],
                capture_output=True, text=True).stdout.strip()
    if found and found != "null":
        run(["gh", "issue", "comment", found, "-R", ISSUES_REPO, "--body", text], check=True)
        return f"commented on #{found}"
    r = run(["gh", "issue", "create", "-R", ISSUES_REPO, "--title", ISSUE_TITLE, "--body", text,
             "--label", "type:problem,priority:low,machine:production,machine:r9"], capture_output=True, text=True)
    return "opened " + r.stdout.strip() if r.returncode == 0 else f"issue create failed: {r.stderr.strip()[:200]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--freshness", action="store_true")
    ap.add_argument("--no-discord", action="store_true", help="skip the Discord post (recovering from a partial run)")
    a = ap.parse_args()
    env = load_env()
    base = env.get("LOKI_URL", "http://100.121.244.45:3100")

    if a.freshness:
        stale = []
        for h, t in last_seen_per_host(base).items():
            if t is None or time.time() - t > 3600:
                stale.append(f"{h}: " + ("no logs in 3 days" if t is None else f"last log {ago(time.time()-t)} ago"))
        if stale:
            print("LOG PIPELINE STALE: " + "; ".join(stale)); return 1
        print("ok: all hosts sending logs"); return 0

    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        state = json.load(open(STATE_FILE))
    except (FileNotFoundError, ValueError):
        state = {}
    first_run = "signatures" not in state
    end, rep = build(base)
    text, new, still, gone = compose(end, rep, state, first_run)
    stale = [h for h, t in last_seen_per_host(base).items() if t is None or time.time() - t > 3600]
    if stale:
        text += f"\n\n⚠️ **No recent logs from: {', '.join(stale)}.** The log collector may be down, so this digest may be missing things."
    if a.dry_run:
        print(text); print(f"\n[dry-run] new={len(new)} still={len(still)} gone={len(gone)} total_repeating={len(rep)}")
        return 0
    webhook = env.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("DISCORD_WEBHOOK_URL missing in log-digest.env", file=sys.stderr); return 2
    if not a.no_discord:
        post_discord(webhook, text)
    state["signatures"] = {sigid(*k): {"sig": k[1], "first_digest": state.get("signatures", {}).get(sigid(*k), {}).get("first_digest", int(end))} for k in rep}
    state["last_run"] = int(end)
    json.dump(state, open(STATE_FILE, "w"))          # saved BEFORE GitHub so a GitHub failure can never cause a re-post
    msg = "discord ok" if not a.no_discord else "discord skipped"
    if new:
        try:
            msg += "; github: " + github_issue(new, rep, end, first_run)
        except Exception as e:
            msg += f"; github FAILED: {e}"
    print(datetime.now().isoformat(timespec="seconds"), msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
