#!/usr/bin/env python3
"""Three-state gate: PASS / FAIL (positive evidence of mismatch) / UNVERIFIABLE (too few usable windows, nothing contradicts).
usage: score3.py MEASUREMENTS.jsonl"""
import json, sys, itertools, collections, statistics
GOOD=set(json.load(open('good_titles.json')))
R=[r for r in (json.loads(l) for l in open(sys.argv[1])) if r['title'] in GOOD]
def feats(r):
    ws=[w for w in r['windows'] if w]
    cues=[w['cues'] for w in ws]
    return {'nwin':len(ws),'offs':[w['offset'] for w in ws],'cues':cues,'cov':(r['last_cue_end']/r['dur']) if r['dur'] else 0}
def state(r,p):
    c,m,t,sp,nw,q,rr=p; f=feats(r); offs=f['offs']
    use=[o for o in offs if abs(o)<=c]; big=[o for o in offs if abs(o)>c]
    if f['nwin']<nw or (q and f['cov']<q): return 'FAIL'
    if rr and f['cues'] and min(f['cues'])<rr*statistics.median(f['cues']): return 'FAIL'   # a window with far fewer cues than its siblings = missing dialogue
    if use and (max(abs(o) for o in use)>t or max(use)-min(use)>sp): return 'FAIL'
    # consensus large offset: several windows agree on the same big shift (a real shift, not scattered artifacts)
    for b in big:
        if sum(1 for o in big if abs(o-b)<=2.0)>=3: return 'FAIL'
    if len(use)<m: return 'UNVERIFIABLE'
    return 'PASS'
def category(v):
    if v=='orig': return 'orig'
    if v.startswith('shift'): return 'shift>=5s' if abs(int(v[5:]))>=5 else 'shift1s'
    if v.startswith('scale'): return 'scale'
    if v.startswith('trunc'): return 'trunc'
    if v=='gap10min': return 'gap'
    if v=='step+45': return 'step'
    if v.startswith('drift'): return 'drift'
TRAIN={'sop_s01e%02d'%i for i in (1,3,5,7,9,11,13)}|{'jumanji'}
def tally(rows,p):
    d=collections.defaultdict(collections.Counter)
    for r in rows: d[category(r['variant'])][state(r,p)]+=1
    return d
grid=list(itertools.product((10,),(3,4,5),(0.5,1.0),(1.0,2.0),(6,7),(0.85,0.9),(0,0.3,0.45)))
tr=[r for r in R if r['title'] in TRAIN]; te=[r for r in R if r['title'] not in TRAIN]
cands=[]
for p in grid:
    d=tally(tr,p)
    if d['orig']['FAIL']>0: continue
    sc=sum(v['FAIL'] for k,v in d.items() if k!='orig')+3*d['shift>=5s']['FAIL']-2*d['orig']['UNVERIFIABLE']
    cands.append((sc,p))
cands.sort(reverse=True); top=[p for s,p in cands if s==cands[0][0]]
top.sort(key=lambda p:(p[2],p[3],-p[6]))   # prefer the most sensitive thresholds among ties
p=top[0]
print('chosen (cutoff,min_usable,max_off,spread,min_windows,min_coverage,sparse_ratio)=',p,'| tied',len(top))
for name,rows in (('TRAIN',tr),('TEST',te),('ALL',R)):
    d=tally(rows,p); o=d['orig']
    print(f"\n{name}: untouched good subs -> PASS {o['PASS']}  UNVERIFIABLE {o['UNVERIFIABLE']}  FAIL(false positive) {o['FAIL']}  of {sum(o.values())}")
    for k in ('shift>=5s','shift1s','scale','trunc','gap','step','drift'):
        v=d[k]; n=sum(v.values()); print(f"   {k:10s} FAIL {v['FAIL']:3d}/{n}  unverifiable {v['UNVERIFIABLE']}  PASSED(miss) {v['PASS']}")
json.dump({'params':dict(zip(['cutoff','min_usable','max_abs_offset','max_spread','min_windows','min_coverage','sparse_ratio'],p))},open('chosen_rule3.json','w'),indent=1)
print('\nmisses:')
for r in R:
    if r['variant']!='orig' and state(r,p)=='PASS': print('  ',r['title'],r['variant'],[round(o,1) for o in feats(r)['offs']],feats(r)['cues'])
print('\nambiguous current files and the real known-bad set (earlier labelling run):')
for f in ('measurements.jsonl','../../talkarr-trial/ours/labels.jsonl'):
    for l in open(f):
        d=json.loads(l)
        if 'windows' not in d: continue
        name=d.get('title') or d.get('tag')
        if f=='measurements.jsonl' and not (d['variant']=='orig' and name in ('sith','gethard','uncharted')): continue
        if f!='measurements.jsonl' and name.startswith('c0') is False: continue
        print('  ',name[:34].ljust(34),state(d,p),[round(w['offset'],1) if w else None for w in d['windows']])
