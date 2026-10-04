#!/usr/bin/env python3
"""Peak-VRAM probe for subgen (Whisper large-v3) and Immich ML on r9. Gated by plexload's watchdog check."""
import json, subprocess, threading, time, sys, os
import plexload as p

IMM = "http://100.121.244.45:3003/predict"
SUB = "http://100.121.244.45:9000/asr?task=transcribe&language=en&output=srt&encode=true"
ENTRIES = json.dumps({
  "facial-recognition": {"detection": {"modelName": "buffalo_l", "options": {"minScore": 0.7}}, "recognition": {"modelName": "buffalo_l"}},
  "clip": {"visual": {"modelName": "ViT-B-32__openai"}},
  "ocr": {"detection": {"modelName": "PP-OCRv5_mobile", "options": {"minScore": 0.5, "maxResolution": 736}},
          "recognition": {"modelName": "PP-OCRv5_mobile", "options": {"minScore": 0.5}}}})

peak = {"mem": 0, "procs": ""}
stop = threading.Event()

def sampler(log):
    while not stop.is_set():
        m = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip().split(",")
        pr = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
        mem = int(m[0]); log.write("%.1f,%s,%s,%s\n" % (time.time(), mem, m[1].strip(), pr.replace("\n", ";"))); log.flush()
        if mem > peak["mem"]: peak["mem"], peak["procs"] = mem, pr.replace("\n", "; ")
        time.sleep(0.4)

def curl(args, label, out):
    t = time.time()
    r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "300"] + args, capture_output=True, text=True)
    out[label] = (r.stdout, round(time.time() - t, 1))

def guarded(threads, results):
    for t in threads: t.start()
    while any(t.is_alive() for t in threads):
        why = p.foreign_activity()
        if why:
            print("ABORT (no further work sent):", why, flush=True); return False
        time.sleep(2)
    return True

why = p.foreign_activity()
if why: print("REFUSING:", why); sys.exit(2)
base = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout)
print("baseline VRAM MiB:", base, flush=True)
log = open(os.path.expanduser("~/loadtest/vramprobe.csv"), "w")
threading.Thread(target=sampler, args=(log,), daemon=True).start()

def run(label, jobs):
    peak["mem"] = 0; res = {}
    ths = [threading.Thread(target=curl, args=(a, l, res)) for l, a in jobs]
    ok = guarded(ths, res)
    print("%-28s http/secs %s | peak VRAM %d MiB (+%d over baseline) | at peak: %s" % (label, res, peak["mem"], peak["mem"] - base, peak["procs"]), flush=True)
    return ok

sub = ("subgen", ["-F", "audio_file=@/home/jay/loadtest/clip.aac", SUB])
imm = ("immich", ["-F", "entries=" + ENTRIES, "-F", "image=@/home/jay/loadtest/poster.jpg", IMM])
if run("subgen alone (4 min speech)", [sub]):
    time.sleep(20)
    if run("immich ML alone (face+clip+ocr)", [imm]):
        time.sleep(20)
        run("subgen + immich together", [sub, imm])
print("idle wait 75s to watch unload...", flush=True)
time.sleep(75)
now = int(subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout)
print("VRAM after idle wait: %d MiB (baseline %d)" % (now, base))
stop.set()
