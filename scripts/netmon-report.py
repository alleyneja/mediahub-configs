#!/usr/bin/env python3
"""Summarise ~/logs/netmon/netmon.csv (written by netmon.py). See docs/netmon.md.
   netmon-report.py [--days N]      (default: everything logged)
Answers: how fast is the uplink, does it dip at certain hours, how much does latency rise under load (bufferbloat),
what was Plex doing at the time, and where are the gaps (tests skipped because someone was streaming)."""
import csv, datetime, os, statistics, sys

PATH = os.path.expanduser('~/logs/netmon/netmon.csv')


def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def vals(rows, col): return [x for x in (f(r.get(col)) for r in rows) if x is not None]


def stat(xs):
    return f'n={len(xs):<3} min {min(xs):6.1f}  med {statistics.median(xs):6.1f}  max {max(xs):6.1f}' if xs else 'no data'


def main():
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else None
    rows = list(csv.DictReader(open(PATH)))
    if days:
        cut = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%F %T')
        rows = [r for r in rows if r['ts_local'] >= cut]
    if not rows: print('no rows'); return
    tests = [r for r in rows if r['kind'] in ('upload', 'full')]
    skipped = [r for r in rows if r['skip_reason']]
    print(f"{rows[0]['ts_local']} .. {rows[-1]['ts_local']}   {len(rows)} runs, {len(tests)} speed tests, "
          f"{len(skipped)} skipped ({sum(r['skip_reason'] == 'remote_streams' for r in skipped)} because of remote streams)")
    gb = sum((f(r.get('pin_bytes_sent')) or 0) + (f(r.get('pin_bytes_recv')) or 0) + (f(r.get('alt_bytes_sent')) or 0)
             + (f(r.get('alt_bytes_recv')) or 0) for r in tests) / 1e9
    print(f'data used by the tests: {gb:.1f} GB')

    print('\n== Speed (Mbps) ==')
    for label, col in (('upload   pinned (fdcservers)', 'pin_up_mbps'), ('upload   alt (Brightspeed)', 'alt_up_mbps'),
                       ('download pinned (fdcservers)', 'pin_down_mbps'), ('download alt (Brightspeed)', 'alt_down_mbps')):
        print(f'{label:<32}{stat(vals(rows, col))}')

    print('\n== Upload by hour of day (best of the two servers, Mbps) ==')
    by = {}
    for r in tests:
        v = [x for x in (f(r.get('pin_up_mbps')), f(r.get('alt_up_mbps'))) if x is not None]
        if v: by.setdefault(int(r['hour']), []).append(max(v))
    for h in sorted(by): print(f'{h:02d}:00  n={len(by[h]):<3} min {min(by[h]):5.1f}  med {statistics.median(by[h]):5.1f}  max {max(by[h]):5.1f}')

    print('\n== Latency (ms, idle, 20 pings per run) ==')
    for label, p in (('gateway', 'gw'), ('1.1.1.1', 'cf'), ('8.8.8.8', 'g8')):
        print(f'{label:<10} avg: {stat(vals(rows, p + "_avg"))}')
        print(f'{"":<10} jitter: {stat(vals(rows, p + "_jitter"))}   runs with loss: {sum(1 for x in vals(rows, p + "_loss") if x > 0)}')

    print('\n== Bufferbloat: latency to 1.1.1.1 under load vs idle (ms) ==')
    for r in tests:
        idle = f(r.get('cf_avg'))
        for p, name in (('pin', 'pinned'), ('alt', 'alt')):
            la, lm = f(r.get(p + '_loaded_avg')), f(r.get(p + '_loaded_max'))
            if la is not None and idle is not None:
                mode = 'down+up' if r['kind'] == 'full' else 'up only'
                print(f"{r['ts_local']}  {name:<8}{mode:<8} idle {idle:5.1f}  loaded avg {la:6.1f}  max {lm:6.1f}  (+{la - idle:.0f} avg)")

    print('\n== Plex activity vs upload ==')
    for r in tests:
        up = [x for x in (f(r.get('pin_up_mbps')), f(r.get('alt_up_mbps'))) if x is not None]
        print(f"{r['ts_local']}  up {max(up) if up else 0:5.1f}  plex streams {r.get('plex_streams') or '-'} "
              f"(wan {f(r.get('plex_wan_kbps')) or 0:.0f} kbps, transcodes {r.get('plex_transcodes') or 0}, burned subs {r.get('plex_burned_subs') or 0})"
              f"  gluetun tx {r.get('ctx_gluetun_tx_kbps') or '-'} kbps")

    busy = [r for r in rows if (f(r.get('plex_wan_kbps')) or 0) > 0]
    if busy:
        print(f'\n== While Plex was streaming remotely ({len(busy)} runs): idle latency to 1.1.1.1 ==')
        print(stat(vals(busy, 'cf_avg')), '  vs quiet:', stat(vals([r for r in rows if r not in busy], 'cf_avg')))
    errs = [r for r in tests if r.get('pin_error') or r.get('alt_error')]
    if errs: print(f'\n{len(errs)} test(s) had errors; first: {errs[0].get("pin_error") or errs[0].get("alt_error")}')


if __name__ == '__main__':
    main()
