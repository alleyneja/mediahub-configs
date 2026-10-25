#!/usr/bin/env python3
"""Synthetic "does it actually work" checks for service-to-service links (issue #71).

Uptime Kuma / docker health only say a container is UP. Seerr->Plex was dead for 4+ weeks (stale host `plex`
resolving to the old machine) while every container was healthy. These checks exercise the links themselves:

  1. *arr health      Sonarr/Radarr/Lidarr/Prowlarr /health and Bazarr /system/health: any ERROR-level item
                      (download client down, indexer dead, root folder missing, app sync failing).
  2. *arr -> Plex     the "Plex (r9)" Connect entry in Sonarr/Radarr/Lidarr via the app's own /notification/test.
  3. Seerr -> Plex    run FROM INSIDE the seer container (its own DNS + network), using the host/port in Seerr's
                      settings.json; the same for Seerr's Radarr and Sonarr entries. This is the check that would
                      have caught the Sep 9 failure.
  4. Log pipeline     log-digest.py --freshness (a host that stopped sending logs to Loki).
  5. Immich pairing   the server (production, moving `release` tag) and r9's machine-learning container (pinned
                      `-cuda` tag) must run the same version (#30). Compares the version label of each RUNNING image.

Posts to Discord only when the SET of problems changes (new problem, or all clear again), like fleet-healthcheck.sh.
Always pings an Uptime Kuma push monitor (up when clean, down with the reason otherwise); Kuma also alerts if no
heartbeat arrives (cron dead, script broken). Read-only: never restarts or changes anything.

  integration-check.py            real run (cron, hourly on production)
  integration-check.py --dry-run  print the result; no Discord, no Kuma, no state change

Config: scripts/integration-check.env (gitignored): DISCORD_WEBHOOK_URL=..., KUMA_PUSH_URL=...
"""
import argparse, json, os, re, subprocess, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, "integration-check.env")
STATE_DIR = os.path.expanduser("~/logs/integration-check")
STATE_FILE = os.path.join(STATE_DIR, "last-problems.json")
SEERR_SETTINGS = "/srv/docker/seer/settings.json"
BAZARR_CONFIG = "/srv/docker/bazarr/config/config.yaml"

# name, config dir, port, API version prefix
ARRS = [("sonarr", "/srv/docker/sonarr", 8989, "v3"), ("radarr", "/srv/docker/radarr", 7878, "v3"),
        ("lidarr", "/srv/docker/lidarr", 8686, "v1"), ("prowlarr", "/srv/docker/prowlarr", 9696, "v1")]


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    return env


def http(url, key=None, method="GET", body=None, timeout=20):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
    if key:
        req.add_header("X-Api-Key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def api_key(cfg_dir):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", open(os.path.join(cfg_dir, "config.xml")).read()).group(1)


def check_arrs(problems):
    for name, cfg, port, ver in ARRS:
        try:
            key = api_key(cfg)
            base = f"http://127.0.0.1:{port}/api/{ver}"
            code, health = http(f"{base}/health", key)
            if code != 200:
                problems.append(f"{name}: /health returned HTTP {code}")
                continue
            for h in health:
                if h.get("type") == "error":
                    problems.append(f"{name} health: {h.get('message', h.get('source'))}"[:220])
            if name == "prowlarr":
                continue
            code, notes = http(f"{base}/notification", key)
            for n in notes if code == 200 else []:
                if n.get("implementation") != "PlexServer":
                    continue
                code, res = http(f"{base}/notification/test", key, "POST", n)
                if code != 200:
                    msg = res if isinstance(res, str) else json.dumps(res)
                    problems.append(f"{name} -> Plex connection '{n['name']}' failed its own test (HTTP {code}): {msg[:140]}")
        except Exception as e:  # a check that cannot run is itself a finding
            problems.append(f"{name}: check could not run ({type(e).__name__}: {e})"[:220])


def check_bazarr(problems):
    try:
        key = re.search(r"^\s*apikey:\s*(\S+)", open(BAZARR_CONFIG).read(), re.M).group(1)
        code, res = http("http://127.0.0.1:6767/api/system/health", key)
        if code != 200:
            problems.append(f"bazarr: /system/health returned HTTP {code}")
        else:
            for h in (res or {}).get("data", []):
                problems.append(f"bazarr health: {h.get('issue', h)}"[:220])
    except Exception as e:
        problems.append(f"bazarr: check could not run ({type(e).__name__}: {e})"[:220])


def from_seerr(url):
    """HTTP status of `url` as seen from inside the seer container (None = no connection)."""
    r = subprocess.run(["docker", "exec", "seer", "wget", "-q", "-S", "-O", "/dev/null", "-T", "8", url],
                       capture_output=True, text=True)
    m = re.search(r"HTTP/\S+\s+(\d{3})", r.stderr)
    return int(m.group(1)) if m else None


def check_seerr(problems):
    try:
        s = json.load(open(SEERR_SETTINGS))
        targets = [("Plex", f"http://{s['plex']['ip']}:{s['plex']['port']}/identity")]
        for kind, path in (("radarr", "/api/v3/system/status"), ("sonarr", "/api/v3/system/status")):
            for e in s.get(kind, []):
                targets.append((f"{kind.capitalize()} '{e.get('name')}'", f"http://{e['hostname']}:{e['port']}{path}"))
        for label, url in targets:
            code = from_seerr(url)
            if code is None:
                problems.append(f"Seerr cannot reach {label} at {url.split('/api')[0].split('/identity')[0]} (no connection from inside the seer container)")
            elif code >= 500:
                problems.append(f"Seerr -> {label} answered HTTP {code}")
    except Exception as e:
        problems.append(f"seerr: check could not run ({type(e).__name__}: {e})"[:220])


def check_logs(problems):
    r = subprocess.run([os.path.join(HERE, "log-digest.py"), "--freshness"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        problems.append("log pipeline: " + (r.stdout.strip() or r.stderr.strip() or "freshness check failed")[:200])


R9_SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "HostKeyAlias=192.168.0.149", "jay@192.168.0.22"]
VERSION_LABEL = '{{index .Config.Labels "org.opencontainers.image.version"}}'


def check_immich(problems):
    try:
        srv = subprocess.run(["docker", "inspect", "immich-server", "--format", VERSION_LABEL],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        ml = subprocess.run(R9_SSH + ["docker", "inspect", "immich-machine-learning", "--format", f"'{VERSION_LABEL}'"],
                            capture_output=True, text=True, timeout=45).stdout.strip()
        if not srv or not ml:
            problems.append(f"immich: could not read versions (server='{srv}', r9 ML='{ml}')")
        elif srv != ml:
            problems.append(f"immich: server is {srv} but r9 machine-learning is {ml}; they must match (re-pin stacks/immich-ml-r9 to v{srv.lstrip('v')}-cuda)")
    except Exception as e:
        problems.append(f"immich: check could not run ({type(e).__name__}: {e})"[:220])


def post_discord(webhook, text):
    req = urllib.request.Request(webhook, data=json.dumps({"content": text[:1900]}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "integration-check"})
    urllib.request.urlopen(req, timeout=15).read()


def kuma(env, status, msg):
    url = env.get("KUMA_PUSH_URL")
    if not url:
        return
    try:
        q = urllib.parse.urlencode({"status": status, "msg": msg[:200], "ping": ""})
        urllib.request.urlopen(f"{url.split('?')[0]}?{q}", timeout=15).read()
    except Exception as e:  # alerting must never break the check
        print(f"WARNING: could not send Kuma heartbeat: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    env = load_env()

    problems = []
    check_arrs(problems); check_bazarr(problems); check_seerr(problems); check_immich(problems); check_logs(problems)
    problems = sorted(set(problems))
    print("OK: all integration checks passed" if not problems else "PROBLEMS:\n- " + "\n- ".join(problems))
    if a.dry_run:
        return 1 if problems else 0

    os.makedirs(STATE_DIR, exist_ok=True)
    last = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else []
    webhook = env.get("DISCORD_WEBHOOK_URL")
    if problems != last and webhook:
        if problems:
            new = [p for p in problems if p not in last]
            text = "**Integration check: something is up but not working**\n" + "\n".join(f"- {p}" for p in problems)
            if new and last:
                text += f"\n({len(new)} new since last check)"
        else:
            text = "**Integration check: all clear again.**"
        post_discord(webhook, text)
    json.dump(problems, open(STATE_FILE, "w"))
    kuma(env, "down" if problems else "up", "; ".join(problems) if problems else "all checks passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
