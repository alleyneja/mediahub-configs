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
LOCK_FILE="/tmp/permissions-monitor.lock"   # same lock permissions-monitor.sh holds while it appends
SNAPSHOT="$(mktemp)"
trap 'rm -f "$SNAPSHOT"' EXIT

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

touch "$DIGEST_FILE"

# Snapshot-and-truncate under the monitor's lock so a run appending mid-digest can't be
# wiped by the truncate. The Discord send happens outside the lock (it can be slow).
(
    flock -w 120 9 || exit 1
    cat "$DIGEST_FILE" > "$SNAPSHOT"
    : > "$DIGEST_FILE"
) 9>"$LOCK_FILE" || { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$HOST_TAG WARNING: digest could not get monitor lock, skipping" >> "$FORENSIC_LOG"; exit 1; }

if [ ! -s "$SNAPSHOT" ]; then
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
done < "$SNAPSHOT"

if send_discord_alert "Daily permissions-monitor digest for **$HOST_TAG**: $total_count routine new anomaly file(s) across $run_count run(s) below the burst threshold, $first_ts to $last_ts. Not auto-fixed - review permissions-forensics.log / permissions-pending-fixes.txt as usual."; then
    :
else
    # Put the snapshot back ahead of anything appended since, under the lock.
    (
        flock -w 120 9 || exit 1
        cat "$SNAPSHOT" "$DIGEST_FILE" > "$SNAPSHOT.merged" && cat "$SNAPSHOT.merged" > "$DIGEST_FILE"
        rm -f "$SNAPSHOT.merged"
    ) 9>"$LOCK_FILE"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$HOST_TAG WARNING: digest send failed, leaving accumulator ($run_count run(s), $total_count file(s)) for next attempt" >> "$FORENSIC_LOG"
fi
