#!/usr/bin/env python3
"""Make controlled corruptions of known-good subs. usage: gen_variants.py GOODLIST.json OUTDIR
GOODLIST: [{"title":..,"srt":..,"video":..(container path)}]  -> OUTDIR/<title>/<variant>.srt + jobs.json"""
import re, json, os, sys
def parse(p):
    t=open(p,encoding='utf-8',errors='replace').read(); o=[]
    for m in re.finditer(r'(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)\s*\n(.*?)(?:\n\n|\Z)',t,re.S):
        g=list(map(int,m.groups()[:8])); o.append((g[0]*3600+g[1]*60+g[2]+g[3]/1e3,g[4]*3600+g[5]*60+g[6]+g[7]/1e3,m.group(9)))
    return o
def ts(x):
    x=max(x,0); ms=round(x*1000); return '%02d:%02d:%02d,%03d'%(ms//3600000,ms%3600000//60000,ms%60000//1000,ms%1000)
def write(p,cues):
    open(p,'w').write(''.join(f'{i+1}\n{ts(s)} --> {ts(e)}\n{x}\n\n' for i,(s,e,x) in enumerate(cues)))
def variants(c):
    last=c[-1][1]; V={'orig':c}
    for k in (-60,-20,-5,-1,1,5,20,60): V['shift%+d'%k]=[(s+k,e+k,x) for s,e,x in c]
    for name,f in (('scale_x0.95904',23.976/25),('scale_x1.04271',25/23.976),('scale_x1.001',1.001)): V[name]=[(s*f,e*f,x) for s,e,x in c]
    for p in (50,90): V['trunc%d'%p]=[q for q in c if q[0]<last*p/100]
    g0=last*0.40; V['gap10min']=[q for q in c if not (g0<=q[0]<g0+600)]
    V['step+45']=[(s+(45 if s>last*0.45 else 0),e+(45 if s>last*0.45 else 0),x) for s,e,x in c]
    for d in (4,8): V['drift%ds'%d]=[(s+d*s/last,e+d*e/last,x) for s,e,x in c]
    return V
good=json.load(open(sys.argv[1])); out=sys.argv[2]; jobs=[]
for g in good:
    c=parse(g['srt']); d=os.path.join(out,g['title']); os.makedirs(d,exist_ok=True)
    for name,cues in variants(c).items():
        p=os.path.join(d,name+'.srt'); write(p,cues)
        jobs.append({'title':g['title'],'variant':name,'srt':p,'video':g['video']})
json.dump(jobs,open(os.path.join(out,'jobs.json'),'w'))
print(len(good),'titles',len(jobs),'jobs')
