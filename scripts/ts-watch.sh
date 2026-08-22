#!/usr/bin/env bash
# Sample the tailnet path to a peer while someone runs a transfer.
# Captures the three things that distinguish "relayed" from "direct but slow":
#   1. CurAddr        - empty means DERP relay, an ip:port means direct
#   2. Relay          - which DERP region is assigned
#   3. rx/tx deltas   - proves bytes actually moved during the window
LOG=/home/jay/ts-watch.log
DUR=${1:-300}
echo "=== WATCH $(date '+%H:%M:%S') for ${DUR}s ===" | tee -a $LOG
end=$(( $(date +%s) + DUR ))
prev_rx=0; prev_tx=0
while [ $(date +%s) -lt $end ]; do
  tailscale status --json 2>/dev/null > /tmp/tsw.json || break
  python3 - <<'PY' | tee -a $LOG
import json,time
try: d=json.load(open('/tmp/tsw.json'))
except: raise SystemExit
for p in d.get('Peer',{}).values():
    if 'jays-iphone' in p.get('DNSName',''):
        ca=p.get('CurAddr') or ''
        path='DERP-RELAY' if ca=='' else 'DIRECT'
        print(f"{time.strftime('%H:%M:%S')}  online={str(p.get('Online')):<5} active={str(p.get('Active')):<5} "
              f"path={path:<10} curaddr={ca or '-':<24} relay={p.get('Relay','-'):<4} "
              f"rx={p.get('RxBytes',0)/1e6:8.2f}MB tx={p.get('TxBytes',0)/1e6:8.2f}MB")
PY
  python3 -c "import time;time.sleep(2)"
done
echo "=== WATCH END ===" | tee -a $LOG
