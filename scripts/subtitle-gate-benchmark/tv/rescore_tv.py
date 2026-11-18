#!/usr/bin/env python3
"""tv-v1.1 rescoring (run in the tvbench dir: out_A.jsonl = benchmark, all_A.jsonl = library pass). MAXOFF 0.75 s plus a one-outlier-window allowance.
Checked against the benchmark: 0/8 untouched fail, 16/16 one-second shifts, all other corruptions as before. Read-only."""
MAXOFF=0.75
import json,collections,re,sys
def st(r,trim):
    ws=[w for w in r['windows'] if w and w['cues']>=10]; offs=[w['offset'] for w in ws]
    if len(ws)<6 or (r['dur'] and r['last_cue_end']/r['dur']<0.9): return 'FAIL'
    def judge(offs,nu):
        use=[o for o in offs if abs(o)<=10]; big=[o for o in offs if abs(o)>10]
        if use and (max(abs(o) for o in use)>MAXOFF or max(use)-min(use)>1.0): return 'FAIL'
        for b in big:
            if sum(1 for o in big if abs(o-b)<=2.0)>=3: return 'FAIL'
        return 'UNVERIFIABLE' if len(use)<nu else 'PASS'
    s=judge(offs,5)
    if s!='FAIL' or not trim: return s
    # drop the single worst window; the other five must pass on their own
    worst=max(offs,key=abs); rest=list(offs); rest.remove(worst)
    use=[o for o in rest if abs(o)<=10]
    if len(use)==5 and max(abs(o) for o in use)<=MAXOFF and max(use)-min(use)<=1.0 and not any(abs(o)>10 and sum(1 for p in rest if abs(p-o)<=2)>=2 for o in rest if abs(o)>10): return 'PASS'
    return 'FAIL'
def cat(v):
    if v=='orig':return 'orig'
    if v.startswith('shift'):return 'shift>=5s' if abs(int(v[5:]))>=5 else 'shift1s'
    for k in('scale','trunc','drift'):
        if v.startswith(k):return k
    return {'gap10min':'gap','step+45':'step'}[v]
B=[json.loads(l) for l in open('out_A.jsonl')]
for trim in (False,True):
    d=collections.defaultdict(collections.Counter)
    for r in B: d[cat(r['variant'])][st(r,trim)]+=1
    print('TRIM' if trim else 'CURRENT', {k:dict(v) for k,v in d.items()})
A=[json.loads(l) for l in open('all_A.jsonl')]
for trim in (False,True):
    for show in('Suits','Hunter'):
        c=collections.Counter(st(r,trim) for r in A if r['title'].startswith(show));print('TRIM' if trim else 'CURRENT',show,dict(c))
for r in A:
    a,b=st(r,False),st(r,True)
    if a!=b: print('  flipped',r['title'],a,'->',b,[round(w['offset'],1) if w else None for w in r['windows']])
