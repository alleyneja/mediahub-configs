#!/bin/bash
# D12 canary (2026-09-25): turns every natural NAS inode eviction into a live test of the lookupcache=none fix.
#
# Root cause (docs/permissions-monitor.md): after the NAS evicts an inode, UGREEN's ugacl mis-renders its mode (000/700)
# to clients that ask by cached NFS file handle; by-name lookups render it correctly. The fix is lookupcache=none on
# /mnt/nas. This canary keeps 200 files in /mnt/nas/.permissions-canary:
#   ctl-*  checked ONLY via /mnt/permissions-canary-control (unfixed mount, cached handles) -> flips when the NAS evicts
#   fix-*  read ONLY via /mnt/media (fixed path)                                              -> must never be denied
# The halves never overlap, because a by-name lookup heals the inode on the NAS. permissions-monitor.sh prunes this dir
# for the same reason. A control flip is sticky until healed, so a 30-min cadence misses nothing. After logging a
# flip, the control files are healed by name so the next eviction registers as a new event.
#
# Discord: fixed path denied -> ALERT (the fix is incomplete). Control flipped with the fixed path clean -> one
# informational post per event (the proof the fix held through a real eviction). Control mount missing -> ALERT.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"
CTL=/mnt/permissions-canary-control/.permissions-canary
FIX=/mnt/media/.permissions-canary
LOG=/home/jay/logs/permissions-canary.log
STATE=/home/jay/logs/permissions-canary.state   # last fixed-path result: ok|denied
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; HOST="$(hostname)"

if ! mountpoint -q /mnt/permissions-canary-control || [ ! -d "$CTL" ]; then
    echo "$TS host=$HOST ERROR control mount or canary dir missing" >> "$LOG"
    send_discord_alert "permissions-canary on **$HOST**: control mount /mnt/permissions-canary-control (or the canary dir) is missing. The canary is blind until it's remounted."
    exit 1
fi

ctl_bad=0
for f in "$CTL"/ctl-*; do [ "$(stat -c %a "$f" 2>/dev/null)" = 777 ] || ctl_bad=$((ctl_bad+1)); done
fix_denied=0
for f in "$FIX"/fix-*; do head -c1 "$f" >/dev/null 2>&1 || fix_denied=$((fix_denied+1)); done
echo "$TS host=$HOST control_flipped=$ctl_bad/100 fixed_denied=$fix_denied/100" >> "$LOG"

prev="$(cat "$STATE" 2>/dev/null || echo ok)"
if [ "$fix_denied" -gt 0 ]; then
    echo denied > "$STATE"
    if [ "$prev" != denied ]; then
        send_discord_alert "permissions-canary on **$HOST**: FIXED path denied $fix_denied/100 canary reads (control flipped: $ctl_bad/100). The lookupcache=none fix is NOT the whole answer. Log: $LOG"
    fi
else
    echo ok > "$STATE"
    if [ "$prev" = denied ]; then
        send_discord_alert "permissions-canary on **$HOST**: fixed path readable again (0/100 denied)."
    fi
fi

if [ "$ctl_bad" -gt 0 ]; then
    if [ "$fix_denied" -eq 0 ]; then
        send_discord_alert "permissions-canary on **$HOST** (info): NAS eviction observed ($ctl_bad/100 control files mis-rendered on the unfixed mount) and the fixed path held (0/100 denied). This is evidence the lookupcache=none fix works."
    fi
    # Heal the control half by name so the next eviction counts as a new event.
    for f in "$FIX"/ctl-*; do stat "$f" >/dev/null 2>&1; done
    echo "$TS host=$HOST control healed by name" >> "$LOG"
fi
