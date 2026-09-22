#!/bin/bash
# Sends one daily Discord digest summarizing "routine" permissions-monitor findings -
# the below-BURST_THRESHOLD runs that permissions-monitor.sh deliberately did NOT
# alert on immediately (mostly 777-on-creation writes, ~single digits to ~10/run).
# Detection, forensic logging, and queueing are unchanged for these findings - this
# script only rolls up the immediate-alert side so the Discord channel isn't muted by
# ~40 routine pings/day. Real incidents (>= BURST_THRESHOLD in one run) still alert
# immediately from permissions-monitor.sh itself and are NOT held for this digest.
#
# Wire into cron once daily on both hosts, e.g.:
#   0 8 * * * /path/to/permissions-digest-send.sh
# If the accumulator is empty, this sends nothing (no "0 findings" spam).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIGEST_FILE="/home/jay/logs/permissions-digest-pending.txt"
FORENSIC_LOG="/home/jay/logs/permissions-forensics.log"
HOST_TAG="$(hostname)"

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

touch "$DIGEST_FILE"

if [ ! -s "$DIGEST_FILE" ]; then
    exit 0
fi

run_count=0
total_count=0
first_ts=""
last_ts=""

while IFS= read -r line; do
    [ -z "$line" ] && continue
    run_count=$((run_count + 1))
    ts=$(echo "$line" | awk '{print $1}')
    count=$(echo "$line" | grep -oE 'count=[0-9]+' | grep -oE '[0-9]+')
    [ -n "$count" ] && total_count=$((total_count + count))
    [ -z "$first_ts" ] && first_ts="$ts"
    last_ts="$ts"
done < "$DIGEST_FILE"

if send_discord_alert "Daily permissions-monitor digest for **$HOST_TAG**: $total_count routine new anomaly file(s) across $run_count run(s) below the burst threshold, $first_ts to $last_ts. Not auto-fixed - review permissions-forensics.log / permissions-pending-fixes.txt as usual."; then
    : > "$DIGEST_FILE"
else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$HOST_TAG WARNING: digest send failed, leaving accumulator ($run_count run(s), $total_count file(s)) for next attempt" >> "$FORENSIC_LOG"
fi
