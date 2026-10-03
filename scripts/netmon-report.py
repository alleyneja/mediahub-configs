#!/usr/bin/env python3
"""Summarise ~/logs/netmon/netmon.csv (written by netmon.py). See docs/netmon.md.
   netmon-report.py [--days N]      (default: everything logged)
Answers: how fast is the uplink, does it dip at certain hours, how much does latency rise under load (bufferbloat),
what was Plex doing at the time, and where are the gaps (tests skipped because someone was streaming)."""
import csv, datetime, glob, os, statistics, sys

PATHS = sorted(glob.glob(os.path.expanduser('~/logs/netmon/netmon*.csv')))   # older files are kept when the column set changes


def f(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def vals(rows, col): return [x for x in (f(r.get(col)) for r in rows) if x is not None]


def stat(xs):
    return f'n={len(xs):<3} min {min(xs):6.1f}  med {statistics.median(xs):6.1f}  max {max(xs):6.1f}' if xs else 'no data'


def main():
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else None
    rows = sorted((r for p in PATHS for r in csv.DictReader(open(p))), key=lambda r: r['ts_local'])
    cut = None
    if days:
        cut = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%F %T')
        rows = [r for r in rows if r['ts_local'] >= cut]
    if not rows: print('no rows'); return
    tests = [r for r in rows if r['kind'] in ('upload', 'full')]
    skipped = [r for r in rows if r['skip_reason']]
    print(f"{rows[0]['ts_local']} .. {rows[-1]['ts_local']}   {len(rows)} runs, {len(tests)} speed tests, "
          f"{len(skipped)} skipped ({sum(r['skip_reason'] in ('remote_streams', 'streams_active') for r in skipped)} because someone was streaming)")
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

    mrows = [r for r in rows if r.get('modem_ok') == '1']
    if mrows:
        print(f'\n== Modem / DOCSIS signal ({len(mrows)} readings; healthy: SNR >= 36 dB, down power -7..+7 dBmV, up power 35..50 dBmV) ==')
        for label, col in (('downstream SNR min (dB)', 'mds_snr_min'), ('downstream power max (dBmV)', 'mds_pwr_max'),
                           ('downstream power min (dBmV)', 'mds_pwr_min'), ('upstream power max (dBmV)', 'mus_pwr_max'),
                           ('OFDM RxMER (dB)', 'mofdm_mer')):
            print(f'{label:<30}{stat(vals(mrows, col))}')
        print(f"locked downstream channels: min {min(vals(mrows, 'mds_locked') or [0]):.0f} of {max(vals(mrows, 'mds_n') or [0]):.0f}; "
              f"readings with a non-64QAM upstream channel: {sum(1 for x in vals(mrows, 'mus_low_mod') if x > 0)}")
        resets = [r['ts_local'] for r in mrows if r.get('m_reset') == '1']
        if resets: print('counter resets (modem rebooted or resynced):', ', '.join(resets))
        unc = [(f(r.get('m_uncorr_delta')), r) for r in mrows if f(r.get('m_uncorr_delta')) is not None]
        print(f"uncorrectable codewords: {sum(u for u, _ in unc):.0f} over {len(unc)} intervals; corrected: {sum(f(r.get('m_corr_delta')) or 0 for _, r in unc):.0f}")
        for u, r in sorted(unc, key=lambda x: -x[0])[:5]:
            if u > 0: print(f"  +{u:.0f} uncorrectable in the 30 min before {r['ts_local']}  (plex streams {r.get('plex_streams') or 0}, wan {f(r.get('plex_wan_kbps')) or 0:.0f} kbps)")
        bad = [r for r in mrows if (f(r.get('mds_snr_min')) or 99) < 36 or (f(r.get('mus_pwr_max')) or 0) > 50]
        if bad: print(f'{len(bad)} reading(s) outside healthy range, first at {bad[0]["ts_local"]}')
    errm = [r for r in rows if r.get('modem_error')]
    if errm: print(f"\n{len(errm)} run(s) could not read the modem; latest: {errm[-1]['modem_error']}")

    # ---- Plex buffering events lined up with line load (minute.csv + buffering-events.csv) --------------------------------
    bp, mp = os.path.expanduser('~/logs/netmon/buffering-events.csv'), os.path.expanduser('~/logs/netmon/minute.csv')
    if os.path.exists(bp):
        events = [e for e in csv.DictReader(open(bp)) if not days or e['ts_local'] >= cut]
        mins = {r['ts_local'][:16]: r for r in csv.DictReader(open(mp))} if os.path.exists(mp) else {}
        windows = []
        for r in tests:                                   # our own speed tests: events inside one are suspect
            a = r.get('test_start') or (datetime.datetime.strptime(r['ts_local'], '%Y-%m-%d %H:%M:%S') + datetime.timedelta(seconds=10)).strftime('%F %T')
            b = r.get('test_end') or (datetime.datetime.strptime(a, '%Y-%m-%d %H:%M:%S') + datetime.timedelta(seconds=110)).strftime('%F %T')
            windows.append((a, b))
        mid = [e for e in events if e['startup'] == '0']
        print(f'\n== Plex buffering events: {len(events)} ({len(mid)} mid-play, {len(events) - len(mid)} at start-up) ==')
        inside = [e for e in events if any(a <= e['ts_local'] <= b for a, b in windows)]
        print(f'inside one of our own speed tests: {len(inside)}   <- the speed test itself may cause these; the gate now skips tests while video plays')
        hi = lambda m: (f(m.get('gluetun_tx_kbps')) or 0) > 10000
        known = [(e, mins.get(e['ts_local'][:16])) for e in events]
        known = [(e, m) for e, m in known if m and e not in inside]
        if mins:
            base = sum(1 for m in mins.values() if hi(m)) / len(mins)
            print(f'qBittorrent sending > 10 Mbps in {100 * base:.0f}% of all logged minutes ({len(mins)} minutes); '
                  f'during buffering events: {sum(1 for _, m in known if hi(m))} of {len(known)} ({100 * sum(1 for _, m in known if hi(m)) / max(len(known), 1):.0f}%)')
            print('  (if the second number is clearly higher than the first, upload contention is implicated; similar = it is not)')
        for e, m in known[-12:]:
            print(f"  {e['ts_local']}  {'start-up' if e['startup'] == '1' else 'mid-play'}  qbit tx {m.get('gluetun_tx_kbps') or '-':>6} kbps  sab rx {m.get('sab_rx_kbps') or '-':>6}  1.1.1.1 {m.get('cf_avg')}/{m.get('cf_max')} ms")
    errs = [r for r in tests if r.get('pin_error') or r.get('alt_error')]
    if errs: print(f'\n{len(errs)} test(s) had errors; first: {errs[0].get("pin_error") or errs[0].get("alt_error")}')


if __name__ == '__main__':
    main()
