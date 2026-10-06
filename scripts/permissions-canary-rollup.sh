#!/bin/bash
# Daily one-line Discord roll-up of permissions-canary.log (replaces the per-eviction info posts, 2026-10-05).
# Counts runs, real NAS evictions (control_flipped>0) and fixed-path denials over the last 24h. A non-zero denial
# count is flagged loudly here too, though permissions-canary.sh already alerts on it immediately.
# Cron once daily, e.g.:  13 8 * * * /path/to/permissions-canary-rollup.sh
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/home/jay/logs/permissions-canary.log
HOST="$(hostname)"
SINCE="$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

read -r runs evictions denied < <(cat "${LOG}.1" "$LOG" 2>/dev/null | awk -v since="$SINCE" '
    /fixed_denied=/ && $1 >= since {
        runs++
        split($3, a, /[=\/]/); if (a[2] > 0) ev++
        split($4, b, /[=\/]/); den += b[2]
    }
    END { print runs+0, ev+0, den+0 }')

if [ "$runs" -eq 0 ]; then
    send_discord_alert "permissions-canary roll-up on **$HOST**: NO canary runs logged in the last 24h. The canary may not be running."
elif [ "$denied" -gt 0 ]; then
    send_discord_alert "permissions-canary roll-up on **$HOST**: FIXED path denied $denied read(s) in the last 24h ($runs runs, $evictions evictions). Investigate: $LOG"
else
    send_discord_alert "permissions-canary roll-up on **$HOST** (24h): $runs runs, $evictions real NAS evictions, fixed path denied 0 times. The lookupcache=none fix is holding."
fi
