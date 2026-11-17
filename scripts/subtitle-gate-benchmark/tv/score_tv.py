#!/usr/bin/env python3
"""Scores TV measurements with the same three-state rule as movie rule v1 (PASS / FAIL / UNVERIFIABLE), parameters scaled to short windows.
usage: score_tv.py OUT_A.jsonl [OUT_B.jsonl ...]   Each file is one window configuration (field 'cfg'). Read-only."""
import collections, itertools, json, sys
def state(r, p):
    cutoff, min_cues, min_win, min_use, max_off, spread, cov_min = p
    ws = [w for w in r['windows'] if w and w['cues'] >= min_cues]
    cov = r['last_cue_end'] / r['dur'] if r['dur'] else 0
    offs = [w['offset'] for w in ws]
    use = [o for o in offs if abs(o) <= cutoff]; big = [o for o in offs if abs(o) > cutoff]
    if len(ws) < min_win or cov < cov_min: return 'FAIL'
    if use and (max(abs(o) for o in use) > max_off or max(use) - min(use) > spread): return 'FAIL'
    for b in big:
        if sum(1 for o in big if abs(o - b) <= 2.0) >= 3: return 'FAIL'
    return 'UNVERIFIABLE' if len(use) < min_use else 'PASS'
def cat(v):
    if v == 'orig': return 'orig'
    if v.startswith('shift'): return 'shift>=5s' if abs(int(v[5:])) >= 5 else 'shift1s'
    if v.startswith('scale'): return 'scale'
    if v.startswith('trunc'): return 'trunc'
    if v == 'gap10min': return 'gap'
    if v == 'step+45': return 'step'
    if v.startswith('drift'): return 'drift'
def tally(rows, p):
    d = collections.defaultdict(collections.Counter)
    for r in rows: d[cat(r['variant'])][state(r, p)] += 1
    return d
for path in sys.argv[1:]:
    R = [json.loads(l) for l in open(path)]
    if not R: continue
    nw = max(len(r['windows']) for r in R); cfg = R[0]['cfg']
    print(f"\n===== config {cfg}: {nw} windows, {len(R)} measurements, {len({r['title'] for r in R})} episodes =====")
    grid = list(itertools.product((10,), (4, 6, 8, 10), (nw - 1, nw), (nw - 2, nw - 1), (0.5, 1.0), (1.0, 2.0), (0.85, 0.9)))
    cands = []
    for p in grid:
        if p[3] > p[2]: continue
        d = tally(R, p)
        if d['orig']['FAIL'] > 0: continue
        sc = sum(v['FAIL'] for k, v in d.items() if k != 'orig') + 3 * d['shift>=5s']['FAIL'] - 2 * d['orig']['UNVERIFIABLE']
        cands.append((sc, p))
    if not cands: print("no parameter set gives zero false positives on the untouched subs"); continue
    cands.sort(reverse=True); top = [p for s, p in cands if s == cands[0][0]]; top.sort(key=lambda p: (p[4], p[5], -p[6])); p = top[0]
    print('chosen (cutoff,min_cues,min_windows,min_usable,max_off,spread,min_coverage) =', p, '| tied', len(top))
    d = tally(R, p); o = d['orig']
    print(f"untouched good subs: PASS {o['PASS']}  UNVERIFIABLE {o['UNVERIFIABLE']}  FAIL(false positive) {o['FAIL']}  of {sum(o.values())}")
    for k in ('shift>=5s', 'shift1s', 'scale', 'trunc', 'gap', 'step', 'drift'):
        v = d[k]; n = sum(v.values()); print(f"   {k:10s} FAIL {v['FAIL']:3d}/{n}  unverifiable {v['UNVERIFIABLE']}  PASSED(miss) {v['PASS']}")
    for r in R:
        if r['variant'] != 'orig' and state(r, p) == 'PASS': print('   miss:', r['title'], r['variant'], [round(w['offset'], 1) if w else None for w in r['windows']])
