#!/bin/bash
# Fleet-wide permissions forensic monitor. Replaces zero-perm-subtitle-monitor.sh
# (subtitles only) with whole-pool coverage of both known anomaly patterns: mode 000
# (unreadable, even by owner) and mode 777 (unexpectedly permissive). See D12 in
# ~/mediahub-cleanup.md for the investigation this supports.
#
# ALERT-FIRST, NEVER AUTO-FIX: this script only detects, logs, and queues. It never
# runs chmod. Run permissions-apply-fix.sh after reviewing the queue to actually fix.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_TAG="$(hostname)"
MEDIA_ROOT="/mnt/media"
STATE_FILE="/home/jay/logs/permissions-monitor-seen.txt"
FORENSIC_LOG="/home/jay/logs/permissions-forensics.log"
PENDING_FILE="/home/jay/logs/permissions-pending-fixes.txt"

mkdir -p /home/jay/logs
touch "$STATE_FILE" "$PENDING_FILE"

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

mapfile -d '' -t bad_000 < <(find "$MEDIA_ROOT" -type f -not -perm -u+r -print0 2>/dev/null)
mapfile -d '' -t bad_777 < <(find "$MEDIA_ROOT" -type f -perm 777 -print0 2>/dev/null)

new_files=()
for f in "${bad_000[@]}" "${bad_777[@]}"; do
    if ! grep -qxF "$f" "$STATE_FILE" 2>/dev/null; then
        new_files+=("$f")
    fi
done

if [ ${#new_files[@]} -eq 0 ]; then
    exit 0
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
{
    echo "=== permissions anomaly detection: $TS host=$HOST_TAG ==="
    echo "New affected files: ${#new_files[@]}"
    for f in "${new_files[@]}"; do
        echo "--- $f ---"
        stat "$f" 2>&1
        branch=$(getfattr --only-values -n user.mergerfs.basepath "$f" 2>/dev/null)
        echo "mergerfs branch: ${branch:-unknown}"
        echo "$f" >> "$STATE_FILE"
        echo "$f" >> "$PENDING_FILE"
    done
    echo "--- mount info for $MEDIA_ROOT ---"
    mount | grep -F "$MEDIA_ROOT" 2>&1
    echo "--- containers currently running or recently exited ---"
    docker ps -a --format '{{.Names}}	{{.Status}}' 2>&1
    "$SCRIPT_DIR/nas-log-snapshot.sh" "$TS" 2>&1
    echo "=== end detection ==="
    echo ""
} >> "$FORENSIC_LOG"

sample=$(printf '%s\n' "${new_files[@]:0:5}")
send_discord_alert "Permissions anomaly on **$HOST_TAG**: ${#new_files[@]} new file(s) at mode 000/777.
Sample:
$sample
Full detail in $FORENSIC_LOG on $HOST_TAG. NOT auto-fixed - review then run permissions-apply-fix.sh."
