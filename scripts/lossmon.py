#!/usr/bin/env python3
"""Packet-loss logger: ~5 pings/s to the gaming PC (Wi-Fi) and to r9 (wired control), one row per minute per target.

Cron runs it every minute; each run pings for ~58 s. See docs/sshmon.md. Log: ~/logs/sshmon/lossmon.csv, bursts.csv."""
import argparse, csv, datetime, fcntl, os, re, statistics, subprocess, sys, threading

TARGETS = {'pc': '192.168.0.206', 'ctrl-r9': '192.168.0.22'}
LOG_DIR = os.path.expanduser('~/logs/sshmon')
CSV_PATH, BURST_PATH, LOCK_PATH = (f'{LOG_DIR}/{n}' for n in ('lossmon.csv', 'bursts.csv', '.lossmon.lock'))
RUN_S, INTERVAL, BURST_MIN = 58, 0.2, 3         # a "burst" is this many replies missing in a row (~0.6 s)
COLUMNS = ['ts_local', 'target', 'sent', 'lost', 'loss_pct', 'longest_gap', 'max_rtt_ms', 'rtt_over_50ms']
BURST_COLUMNS = ['start', 'target', 'missed', 'approx_seconds']
THRESH_LOSS, THRESH_GAP = 1.0, BURST_MIN        # --report flags a minute at/above either


def ping(label, ip, out):
    p = subprocess.run(['ping', '-4', '-n', '-O', '-D', '-i', str(INTERVAL), '-w', str(RUN_S), ip],
                       capture_output=True, text=True, timeout=RUN_S + 10)
    seqs, rtts, ts = {}, [], {}
    for line in p.stdout.splitlines():
        m = re.match(r'\[([\d.]+)\] (?:\d+ bytes from .*icmp_seq=(\d+) .*time=([\d.]+) ms|no answer yet for icmp_seq=(\d+))', line)
        if not m: continue
        if m.group(2):
            seqs[int(m.group(2))] = True; rtts.append(float(m.group(3))); ts[int(m.group(2))] = float(m.group(1))
        else:
            seqs.setdefault(int(m.group(4)), False); ts.setdefault(int(m.group(4)), float(m.group(1)))
    out[label] = (seqs, rtts, ts)


def runs(seqs, ts):
    """Yield (first_seq, length) for each run of missed replies."""
    run = []
    for s in sorted(seqs):
        if not seqs[s]: run.append(s)
        elif run: yield run[0], len(run), ts[run[0]]; run = []
    if run: yield run[0], len(run), ts[run[0]]


def collect():
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    lock = open(LOCK_PATH, 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: return
    out = {}
    th = [threading.Thread(target=ping, args=(l, ip, out)) for l, ip in TARGETS.items()]
    for t in th: t.start()
    for t in th: t.join()
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    new, bnew = not os.path.exists(CSV_PATH), not os.path.exists(BURST_PATH)
    with open(CSV_PATH, 'a', newline='') as f, open(BURST_PATH, 'a', newline='') as b:
        w, bw = csv.writer(f), csv.writer(b)
        if new: w.writerow(COLUMNS)
        if bnew: bw.writerow(BURST_COLUMNS)
        for label, (seqs, rtts, ts) in out.items():
            sent = len(seqs); lost = sum(1 for v in seqs.values() if not v)
            rs = list(runs(seqs, ts))
            w.writerow([now, label, sent, lost, round(100 * lost / sent, 1) if sent else '', max((n for _, n, _ in rs), default=0),
                        max(rtts, default=''), sum(1 for r in rtts if r > 50)])
            for _, n, t0 in rs:
                if n >= BURST_MIN:
                    bw.writerow([datetime.datetime.fromtimestamp(t0).strftime('%Y-%m-%d %H:%M:%S'), label, n, round(n * INTERVAL, 1)])


def report(hours, around):
    if not os.path.exists(CSV_PATH): sys.exit('no data yet')
    rows = list(csv.DictReader(open(CSV_PATH)))
    if around:
        day = datetime.datetime.now().strftime('%Y-%m-%d') if len(around) == 5 else around[:10]
        c = datetime.datetime.strptime(f'{day} {around[-5:]}', '%Y-%m-%d %H:%M')
        lo, hi = c - datetime.timedelta(minutes=10), c + datetime.timedelta(minutes=10)
    else:
        hi = datetime.datetime.now(); lo = hi - datetime.timedelta(hours=hours)
    rows = [r for r in rows if lo <= datetime.datetime.strptime(r['ts_local'], '%Y-%m-%d %H:%M:%S') <= hi]
    print(f'{len(rows)} rows, {lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M}')
    for label in TARGETS:
        rs = [r for r in rows if r['target'] == label]
        if not rs: continue
        sent, lost = sum(int(r['sent']) for r in rs), sum(int(r['lost']) for r in rs)
        bad = [r for r in rs if float(r['loss_pct'] or 0) >= THRESH_LOSS or int(r['longest_gap']) >= THRESH_GAP]
        print(f'  {label:8} {lost}/{sent} lost ({100 * lost / max(sent, 1):.2f}%), {len(bad)} of {len(rs)} minutes bad, '
              f'worst gap {max(int(r["longest_gap"]) for r in rs)} pings (~{max(int(r["longest_gap"]) for r in rs) * INTERVAL:.1f}s)')
    bad = [r for r in rows if r['target'] == 'pc' and (float(r['loss_pct'] or 0) >= THRESH_LOSS or int(r['longest_gap']) >= THRESH_GAP)]
    print(f'\n{len(bad)} bad minutes on pc (loss>={THRESH_LOSS}% or a gap of >={THRESH_GAP} pings)')
    for r in bad[:40]:
        ctrl = next((c for c in rows if c['ts_local'] == r['ts_local'] and c['target'] == 'ctrl-r9'), None)
        print(f'  {r["ts_local"]} pc loss={r["loss_pct"]}% gap={r["longest_gap"]} maxrtt={r["max_rtt_ms"]}ms' + (f'   ctrl-r9 loss={ctrl["loss_pct"]}%' if ctrl else ''))
    if len(bad) > 40: print(f'  ... {len(bad) - 40} more')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--hours', type=float, default=24)
    ap.add_argument('--around', metavar='HH:MM', help='report only +/-10 min around a time (today, or YYYY-MM-DD HH:MM)')
    a = ap.parse_args()
    report(a.hours, a.around) if a.report else collect()
