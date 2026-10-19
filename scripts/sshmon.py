#!/usr/bin/env python3
"""SSH responsiveness monitor: one row per 10 s into ~/logs/sshmon/sshmon.csv.

Why: Oct 2026, Claude/SSH tabs from gaming-pc (Wi-Fi, Tailscale SSH) froze or lagged and neither side's logs showed a
cause. This records the server side and the path continuously so a slow moment can be lined up against it. See
docs/sshmon.md. Run from cron every minute (6 samples, flock-guarded); read it with `sshmon.py --report`.

Every sample: Tailscale ping + LAN ping to the PC (direct vs DERP, ms), the live LAN-OpenSSH sessions (TCP rtt, unacked,
retransmits, queued bytes), how many Tailscale SSH sessions exist, PSI (io/memory/cpu avg10), load, swap in/out rate, and
three "does this host feel slow" probes: a 0.5 s sleep overshoot (scheduler delay), a fork+exec, and a 4 KB write+fsync on /home.
Server-side only: it cannot see a stall inside the Windows terminal itself.

Logs hold LAN/tailnet addresses, so they live outside the repo (the repo is public). Stdlib only.
"""
import argparse, csv, datetime, fcntl, json, os, re, statistics, subprocess, sys, time

PC_TS, PC_LAN = 'gaming-pc', '192.168.0.81'
LOG_DIR = os.path.expanduser('~/logs/sshmon')
CSV_PATH, STATE_PATH, LOCK_PATH, PROBE_PATH = (f'{LOG_DIR}/{n}' for n in ('sshmon.csv', 'state.json', '.lock', '.probe'))
SAMPLES, INTERVAL, ROTATE_BYTES = 6, 10, 25 * 1024 * 1024
COLUMNS = ['ts_local', 'ts_ping_ms', 'ts_path', 'lan_ping_ms', 'lan_sessions', 'tsssh_sessions', 'ssh_max_rtt_ms',
           'ssh_unacked', 'ssh_retrans_delta', 'ssh_max_sendq', 'io_some', 'io_full', 'mem_some', 'mem_full', 'cpu_some',
           'load1', 'swap_in_ps', 'swap_out_ps', 'sleep_over_ms', 'fork_ms', 'fsync_ms', 'ts_endpoint']
# --report thresholds: a row is "slow" if any column crosses its line
THRESH = {'ts_ping_ms': 50, 'lan_ping_ms': 20, 'ssh_max_rtt_ms': 150, 'io_full': 10, 'mem_some': 10, 'sleep_over_ms': 100,
          'fork_ms': 50, 'fsync_ms': 100, 'ssh_retrans_delta': 3}


def run(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ''


def endpoint_kind(via):
    """Which address family the ping used: v6, lan4 (RFC1918), wan4, or the DERP relay name."""
    if 'DERP' in via: return via
    if via.startswith('['): return 'v6'
    return 'lan4' if re.match(r'(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))\.', via) else 'wan4'


def ts_ping():
    out = run(['tailscale', 'ping', '-c', '1', '--timeout=3s', PC_TS], 6)
    m = re.search(r'pong from \S+ \([^)]*\) via (\S+) in ([\d.]+)ms', out)
    if not m:
        return '', 'timeout', ''
    return float(m.group(2)), ('direct' if 'DERP' not in m.group(1) else m.group(1)), endpoint_kind(m.group(1))


def lan_ping():
    m = re.search(r'time=([\d.]+) ms', run(['ping', '-c', '1', '-W', '2', PC_LAN], 4))
    return float(m.group(1)) if m else ''


def lan_sessions():
    """Established connections to sshd on :22. Tailscale SSH never shows here (tailscaled terminates it in userspace)."""
    lines = run(['ss', '-tinH', 'state', 'established', '( sport = :22 )'], 5).splitlines()
    n, rtt, unacked, retrans, sendq = 0, 0.0, 0, 0, 0
    for i in range(0, len(lines) - 1, 2):
        head, info = lines[i].split(), lines[i + 1]
        n += 1
        sendq = max(sendq, int(head[1]) if head and head[1].isdigit() else 0)
        if m := re.search(r'\brtt:([\d.]+)/', info): rtt = max(rtt, float(m.group(1)))
        if m := re.search(r'\bunacked:(\d+)', info): unacked = max(unacked, int(m.group(1)))
        if m := re.search(r'\bretrans:\d+/(\d+)', info): retrans += int(m.group(1))
    return n, rtt, unacked, retrans, sendq


def psi(name):
    out = {}
    try:
        for line in open(f'/proc/pressure/{name}'):
            kind, rest = line.split(None, 1)
            out[kind] = float(re.search(r'avg10=([\d.]+)', rest).group(1))
    except OSError:
        pass
    return out.get('some', ''), out.get('full', '')


def vmstat():
    d = {}
    for line in open('/proc/vmstat'):
        k, v = line.split()
        if k in ('pswpin', 'pswpout'): d[k] = int(v)
    return d


def probes():
    t = time.monotonic(); time.sleep(0.5); over = (time.monotonic() - t - 0.5) * 1000
    t = time.monotonic(); run(['/bin/true'], 5); fork = (time.monotonic() - t) * 1000
    t = time.monotonic()
    try:
        fd = os.open(PROBE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, b'x' * 4096); os.fsync(fd); os.close(fd)
    except OSError:
        pass
    return round(over, 1), round(fork, 1), round((time.monotonic() - t) * 1000, 1)


def sample(state):
    now = time.time()
    tsms, tspath, tsep = ts_ping()
    n, rtt, unacked, retrans, sendq = lan_sessions()
    retrans, state['retrans'] = max(0, retrans - state.get('retrans', retrans)), retrans    # per-session counters are cumulative; a closed session makes the sum drop
    tsssh = len(run(['pgrep', '-f', 'be-child ssh'], 3).split())
    io_s, io_f = psi('io'); mem_s, mem_f = psi('memory'); cpu_s, _ = psi('cpu')
    vm, prev = vmstat(), state.get('vm')
    dt = now - state['t'] if prev and state.get('t') else 0
    sin = round((vm['pswpin'] - prev['pswpin']) / dt, 1) if dt > 0 else ''
    sout = round((vm['pswpout'] - prev['pswpout']) / dt, 1) if dt > 0 else ''
    state.update(t=now, vm=vm)
    over, fork, fsync = probes()
    return [datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tsms, tspath, lan_ping(), n, tsssh, rtt, unacked, retrans,
            sendq, io_s, io_f, mem_s, mem_f, cpu_s, os.getloadavg()[0], sin, sout, over, fork, fsync, tsep]


def collect(count):
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    lock = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return                                   # previous minute's run still going
    try:
        state = json.load(open(STATE_PATH))
    except (OSError, ValueError):
        state = {}
    if os.path.exists(CSV_PATH) and os.path.getsize(CSV_PATH) > ROTATE_BYTES:
        os.replace(CSV_PATH, CSV_PATH + '.old')
    if os.path.exists(CSV_PATH) and open(CSV_PATH).readline().strip() != ','.join(COLUMNS):
        os.replace(CSV_PATH, CSV_PATH + '.old')  # columns changed: report reads each file with its own header
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, 'a', newline='') as f:
        w = csv.writer(f)
        if new: w.writerow(COLUMNS)
        for i in range(count):
            t0 = time.monotonic()
            w.writerow(sample(state)); f.flush()
            if i < count - 1: time.sleep(max(0, INTERVAL - (time.monotonic() - t0)))
    json.dump(state, open(STATE_PATH, 'w'))


def report(hours, around):
    rows = []
    for path in (CSV_PATH + '.old', CSV_PATH):
        if os.path.exists(path): rows += list(csv.DictReader(open(path)))
    if not rows: sys.exit('no data yet')
    if around:
        day = datetime.datetime.now().strftime('%Y-%m-%d') if len(around) == 5 else around[:10]
        c = datetime.datetime.strptime(f'{day} {around[-5:]}', '%Y-%m-%d %H:%M')
        lo, hi = c - datetime.timedelta(minutes=10), c + datetime.timedelta(minutes=10)
    else:
        hi = datetime.datetime.now(); lo = hi - datetime.timedelta(hours=hours)
    rows = [r for r in rows if lo <= datetime.datetime.strptime(r['ts_local'], '%Y-%m-%d %H:%M:%S') <= hi]
    print(f'{len(rows)} samples, {lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M}')
    num = lambda r, k: float(r[k]) if r[k] not in ('', None) else None
    for k in THRESH:
        vals = sorted(v for r in rows if (v := num(r, k)) is not None)
        if vals: print(f'  {k:18} p50={statistics.median(vals):>7.1f}  p95={vals[int(len(vals) * .95) - 1]:>7.1f}  max={vals[-1]:>8.1f}')
    miss = sum(1 for r in rows if r['ts_path'] != 'direct')
    print(f'  tailscale path not direct/timeout: {miss} of {len(rows)} ({", ".join(sorted({r["ts_path"] for r in rows if r["ts_path"] != "direct"})) or "-"})')
    kinds = [k for r in rows if (k := r.get('ts_endpoint'))]
    if kinds:
        switches = sum(1 for a, b in zip(kinds, kinds[1:]) if a != b)
        print(f'  endpoint used: {", ".join(f"{k}={kinds.count(k)}" for k in sorted(set(kinds)))}; {switches} switches')
    flagged = [r for r in rows if r['ts_path'] != 'direct' or any((v := num(r, k)) is not None and v >= t for k, t in THRESH.items())]
    print(f'\n{len(flagged)} slow samples (any column over its line: {", ".join(f"{k}>={t}" for k, t in THRESH.items())})')
    for r in flagged[:40]:
        print('  ' + r['ts_local'], ' '.join(f'{k}={r[k]}' for k in ('ts_path', 'ts_endpoint', 'ts_ping_ms', 'lan_ping_ms', 'ssh_max_rtt_ms', 'io_full', 'sleep_over_ms', 'fsync_ms') if r.get(k) not in ('', None)))
    if len(flagged) > 40: print(f'  ... {len(flagged) - 40} more')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--once', action='store_true', help='take a single sample (testing)')
    ap.add_argument('--report', action='store_true', help='summarise the log instead of sampling')
    ap.add_argument('--hours', type=float, default=24, help='report window (default 24)')
    ap.add_argument('--around', metavar='HH:MM', help='report only +/-10 min around a time (today, or YYYY-MM-DD HH:MM)')
    a = ap.parse_args()
    if a.report: report(a.hours, a.around)
    else: collect(1 if a.once else SAMPLES)
