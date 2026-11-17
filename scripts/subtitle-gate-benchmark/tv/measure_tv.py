#!/usr/bin/env python3
"""Runs INSIDE the bazarr container. usage: measure_container.py JOBS.json OUT.jsonl [WORKERS]
Same method as audio_windows.py (7 x 480 s windows, ffsubsync offset), but each window's audio is extracted once per video."""
import re,subprocess,sys,os,json,shutil
from multiprocessing import Pool
import os as _os
CONFIGS={"A":(180,(0.10,0.26,0.42,0.58,0.74,0.90)),"B":(300,(0.10,0.30,0.50,0.70,0.90))}
CFG=_os.environ.get("TV_CFG","A"); W,F=CONFIGS[CFG]
def parse(p):
    t=open(p,encoding='utf-8',errors='replace').read();o=[]
    for m in re.finditer(r'(\d+):(\d+):(\d+)[,.](\d+) --> (\d+):(\d+):(\d+)[,.](\d+)\s*\n(.*?)(?:\n\n|\Z)',t,re.S):
        g=list(map(int,m.groups()[:8]));o.append((g[0]*3600+g[1]*60+g[2]+g[3]/1e3,g[4]*3600+g[5]*60+g[6]+g[7]/1e3,m.group(9)))
    return o
def ts(x):
    x=max(x,0);return '%02d:%02d:%02d,%03d'%(x//3600,x%3600//60,x%60,round(x%1*1000)%1000)
def pick_audio(video):
    """Index (among audio streams) of the first English, non-commentary track; 0 if none qualifies. A film whose first track is a
    dub or a commentary is then measured against the English dialogue. Mirrored by pick_audio_index() in subtitle-enforce.py."""
    try:
        out=subprocess.run(['ffprobe','-v','error','-select_streams','a','-show_entries','stream_tags=language,title','-of','json',video],capture_output=True,text=True,timeout=60).stdout
        st=json.loads(out).get('streams',[])
    except Exception:
        return 0
    for i,x in enumerate(st):
        t={k.lower():str(v) for k,v in (x.get('tags') or {}).items()}
        if t.get('language','').lower() in ('eng','en') and 'comment' not in t.get('title','').lower(): return i
    return 0
def video_job(args):
    video,jobs,outp=args
    tag=re.sub(r'\W+','_',os.path.basename(video))[:40]+str(os.getpid()); work=f'/tmp/benchtv{CFG}/'+tag; os.makedirs(work,exist_ok=True)
    dur=float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',video],capture_output=True,text=True).stdout)
    ai=pick_audio(video); wavs={}
    for i,f in enumerate(F):
        t0=max(0,dur*f-W/2); wav=f'{work}/w{i}.wav'
        subprocess.run(['nice','-n','19','ffmpeg','-v','error','-y','-ss',str(t0),'-t',str(W),'-i',video,'-map',f'0:a:{ai}','-ac','1','-ar','16000',wav],check=True)
        wavs[i]=(t0,wav)
    for j in jobs:
        cues=parse(j['srt']); res={'cfg':CFG,'audio':ai,'title':j['title'],'variant':j['variant'],'dur':round(dur),'cues':len(cues),'last_cue_end':round(cues[-1][1]) if cues else 0,'windows':[]}
        for i,(t0,wav) in wavs.items():
            sel=[(s-t0,e-t0,x) for s,e,x in cues if t0<=s<t0+W]
            if len(sel)<4: res['windows'].append(None); continue
            inp=f'{work}/in.srt'; open(inp,'w').write(''.join(f'{k+1}\n{ts(s)} --> {ts(e)}\n{x}\n\n' for k,(s,e,x) in enumerate(sel)))
            r=subprocess.run(['nice','-n','19','python3','-c','import sys;from ffsubsync.ffsubsync import main;sys.exit(main())',wav,'-i',inp,'-o',work+'/o.srt','--max-offset-seconds','60','--no-fix-framerate','--gss'],capture_output=True,text=True,cwd='/tmp',env={**os.environ,'PYTHONPATH':'/app/bazarr/bin/libs'})
            m=re.findall(r'offset seconds: ([-\d.]+)',r.stdout+r.stderr);sc=re.findall(r'score: ([-\d.]+)',r.stdout+r.stderr)
            res['windows'].append({'at_min':round((t0+W/2)/60),'offset':float(m[-1]),'score':float(sc[-1]),'cues':len(sel)} if m and sc else None)
        with open(outp,'a') as fh: fh.write(json.dumps(res)+'\n')
    shutil.rmtree(work,ignore_errors=True)   # ~100 MB of wav per video
    return video
if __name__=='__main__':
    jobs=json.load(open(sys.argv[1])); outp=sys.argv[2]; n=int(sys.argv[3]) if len(sys.argv)>3 else 3
    done=set()
    if os.path.exists(outp):
        for l in open(outp): r=json.loads(l); done.add((r['title'],r['variant']))
    byv={}
    for j in jobs:
        if (j['title'],j['variant']) not in done: byv.setdefault(j['video'],[]).append(j)
    with Pool(n) as p:
        for v in p.imap_unordered(video_job,[(v,js,outp) for v,js in byv.items()]): print('done',v,flush=True)
