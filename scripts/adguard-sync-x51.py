#!/usr/bin/env python3
"""One-way sync: production AdGuard Home config -> x51 replica.

Reads production's live AdGuardHome.yaml (root-only, via sudo), applies the x51-specific
overrides (its own admin user, plain-UDP upstreams), and pushes it to x51 only if the two
differ in anything that matters. x51's AdGuard is stopped around the write because AdGuard
rewrites its config on shutdown. Needs: PyYAML, ssh key access to x51, NOPASSWD sudo on both.

Every run (in sync or not) also asks x51 for a .lan rewrite and compares it with production's answer, and
reports the outcome to an Uptime Kuma push monitor (KUMA_PUSH_URL in the env file): up on success, down with
the reason on any failure. Kuma also alerts if no heartbeat arrives for 2 h (cron dead, script broken).

Credentials: stacks/adguard-x51/adguard-x51.env (gitignored). Design: docs/dns-resilience.md.
Usage: adguard-sync-x51.py [--dry-run] [--force]
"""
import argparse, copy, os, subprocess, sys, tempfile, time, urllib.parse, urllib.request
import yaml

X51 = "192.168.0.20"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", X51]
PROD_YAML = "/srv/docker/adguardhome/conf/AdGuardHome.yaml"
X51_DIR = "/srv/docker/adguardhome"
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stacks", "adguard-x51", "adguard-x51.env")

# x51 differs from production ONLY here. Plain UDP first so a DoH/TCP-443 stall on production's
# path does not take the replica down too; DoH as the fallback for a UDP-specific failure.
X51_UPSTREAM = ["9.9.9.10", "1.1.1.1"]
X51_FALLBACK = ["https://dns10.quad9.net/dns-query"]


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def load_env():
    env = {}
    for line in open(ENV_FILE):
        if "=" in line and not line.startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v
    return env


def heartbeat(env, status, msg):
    url = env.get("KUMA_PUSH_URL")
    if not url:
        return
    try:
        q = urllib.parse.urlencode({"status": status, "msg": msg[:200], "ping": ""})
        urllib.request.urlopen(f"{url.split('?')[0]}?{q}", timeout=15).read()
    except Exception as e:  # never let alerting break the sync itself
        print(f"WARNING: could not send Kuma heartbeat: {e}", file=sys.stderr)


def check_answers(prod, tries=12, wait=5):
    """x51 must answer a .lan rewrite exactly as production's config says (AdGuard needs a while to load blocklists)."""
    rw = prod["filtering"]["rewrites"][0]
    for _ in range(tries):
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", f"@{X51}", rw["domain"], "A"],
                             capture_output=True, text=True).stdout.split()
        if out == [rw["answer"]]:
            return
        time.sleep(wait)
    raise RuntimeError(f"x51 does not answer {rw['domain']} with {rw['answer']} (got {out})")


def build_replica(prod, env):
    d = copy.deepcopy(prod)
    d["users"] = [{"name": env["ADGUARD_X51_USER"], "password": env["ADGUARD_X51_PASSWORD_BCRYPT"]}]
    d["dns"]["upstream_dns"] = list(X51_UPSTREAM)
    d["dns"]["fallback_dns"] = list(X51_FALLBACK)
    return d


def normalize(doc):
    """Strip what legitimately differs between two healthy instances, for comparison only."""
    d = copy.deepcopy(doc)
    for k in ("users",):
        d.pop(k, None)
    for k in ("upstream_dns", "fallback_dns"):
        d["dns"].pop(k, None)
    for key in ("filters", "whitelist_filters"):
        for f in d.get(key) or []:
            f.pop("last_updated", None)
            f.pop("rules_count", None)
    return d


def diff_keys(a, b):
    na, nb = normalize(a), normalize(b)
    out = []
    for k in sorted(set(na) | set(nb)):
        if na.get(k) != nb.get(k):
            if isinstance(na.get(k), dict) and isinstance(nb.get(k), dict):
                out += [f"{k}.{s}" for s in sorted(set(na[k]) | set(nb[k])) if na[k].get(s) != nb[k].get(s)]
            else:
                out.append(k)
    return out


def sync(args, env):
    prod = yaml.safe_load(run(["sudo", "-n", "cat", PROD_YAML]))
    replica = build_replica(prod, env)
    try:
        live = yaml.safe_load(run(SSH + ["sudo", "-n", "cat", f"{X51_DIR}/conf/AdGuardHome.yaml"]))
    except subprocess.CalledProcessError:
        live = {"dns": {}}
    diffs = diff_keys(replica, live)
    if not diffs and not args.force:
        check_answers(prod, tries=2, wait=2)
        return "in sync, x51 answering"
    print("differs in:", ", ".join(diffs) or "(forced)")
    if args.dry_run:
        return None

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as t:
        yaml.safe_dump(replica, t, sort_keys=False, default_flow_style=False)
    try:
        run(["scp", "-q", "-o", "BatchMode=yes", t.name, f"{X51}:/tmp/AdGuardHome.yaml.new"])
    finally:
        os.unlink(t.name)
    run(SSH + [
        f"cd {X51_DIR} && docker compose stop -t 20 && "
        f"sudo -n install -m 600 -o root -g root /tmp/AdGuardHome.yaml.new conf/AdGuardHome.yaml && "
        f"rm -f /tmp/AdGuardHome.yaml.new && docker compose start"
    ])
    check_answers(prod)
    left = diff_keys(replica, yaml.safe_load(run(SSH + ["sudo", "-n", "cat", f"{X51_DIR}/conf/AdGuardHome.yaml"])))
    if left:
        raise RuntimeError(f"still differs after push: {', '.join(left)}")
    return "pushed and restarted x51 AdGuard; verified"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    env = load_env()
    try:
        result = sync(args, env)
    except Exception as e:
        detail = (e.stderr.strip() if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e))
        print(f"FAILED: {detail}", file=sys.stderr)
        if not args.dry_run:
            heartbeat(env, "down", f"sync failed: {detail}")
        return 1
    if result:
        print(result)
        if not args.dry_run:
            heartbeat(env, "up", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
