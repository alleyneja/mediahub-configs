import json, sys, copy, collections, importlib.util
sys.path.insert(0, '/home/jay/mediahub-configs/scripts')
spec = importlib.util.spec_from_file_location('g', '/home/jay/mediahub-configs/scripts/subtitle-gate.py'); g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)
B = '/home/jay/subtitle-audit-tools/benchmark/'
GOOD = set(json.load(open(B + 'good_titles.json')))
rows = [json.loads(l) for l in open(B + 'measurements.jsonl')]
rows = [r for r in rows if r['title'] in GOOD and 'windows' in r]
p1 = g.load_rule(); p11 = copy.deepcopy(p1); p11['min_coverage_last_cue_over_duration'] = 0.7
def cat(v):
    if v == 'orig': return 'orig'
    if v.startswith('shift'): return 'shift>=5s' if abs(int(v[5:])) >= 5 else 'shift1s'
    for k in ('scale', 'trunc'):
        if v.startswith(k): return k
    return {'gap10min': 'gap', 'step+45': 'step'}.get(v, 'drift' if v.startswith('drift') else v)
for name, p in (('v1 (0.9)', p1), ('v1.1 (0.7)', p11)):
    d = collections.defaultdict(collections.Counter)
    for r in rows: d[cat(r['variant'])][g.judge(r, p)[0]] += 1
    print(name, '| orig FP', d['orig']['FAIL'], 'of', sum(d['orig'].values()), '|', ' '.join(f"{k}:{d[k]['FAIL']}/{sum(d[k].values())}" for k in ('shift>=5s','shift1s','scale','trunc','gap','step','drift')))
print('\nthis session\'s gated downloads under both rules:')
seen = {}
for l in open('/home/jay/logs/subtitle-gate/ledger.jsonl'):
    d = json.loads(l); seen[d.get('key') or d.get('path') or l[:40]] = d
for d in seen.values():
    if 'windows' in d:
        print(' ', (d.get('name') or d.get('title') or d.get('label') or '?')[:34], g.judge(d, p1)[0], '->', g.judge(d, p11)[0])
print('\ntruncation variants by coverage threshold:')
tr = [r for r in rows if r['variant'].startswith('trunc')]
print(' variants:', sorted({r['variant'] for r in tr}))
for t in (0.9, 0.85, 0.8, 0.75, 0.7):
    p = copy.deepcopy(p1); p['min_coverage_last_cue_over_duration'] = t
    per = collections.Counter(); tot = collections.Counter()
    for r in tr:
        tot[r['variant']] += 1; per[r['variant']] += g.judge(r, p)[0] == 'FAIL'
    fp = sum(g.judge(r, p)[0] == 'FAIL' for r in rows if r['variant'] == 'orig')
    print(f' {t}: FP {fp}/16 |', ' '.join(f"{v}:{per[v]}/{tot[v]}" for v in sorted(tot)))
home = [d for d in map(json.loads, open('/home/jay/logs/subtitle-gate/ledger.jsonl')) if 'Home' in json.dumps(d)[:300]]
for d in home[:1]: print('\nHome cov', d.get('last_cue_end'), d.get('dur'), round(d['last_cue_end']/d['dur'],3) if d.get('dur') else '')
