#!/bin/bash
# Detects subtitle files that have landed at permission 000 - a recurring, unexplained bug
# (see gotcha_subtitle_zero_perm_files in project memory: cleaned up twice already, Aug 2026
# and Sep 16 2026, writer never identified). Rather than clean up blind again next time,
# this captures forensic detail about each *newly seen* occurrence - full stat (mtime vs
# ctime tells us whether it was written that way or changed later), mount info, and what
# containers were running/recently restarted - BEFORE fixing the permission, so the next
# occurrence might actually leave enough of a trail to identify the cause.
#
# Meant to run frequently (every 10-15 min) via cron so forensic detail is captured close
# to when the bug actually happens, not hours/days later once nothing correlates anymore.

set -uo pipefail

STATE_FILE="/home/jay/logs/zero-perm-subtitle-seen.txt"
FORENSIC_LOG="/home/jay/logs/zero-perm-subtitle-forensics.log"
touch "$STATE_FILE"

mapfile -d '' -t bad_files < <(find /mnt/media -iname "*.srt" -not -perm -u+r -print0 2>/dev/null)

new_files=()
for f in "${bad_files[@]}"; do
    if ! grep -qxF "$f" "$STATE_FILE"; then
        new_files+=("$f")
    fi
done

if [ ${#new_files[@]} -eq 0 ]; then
    exit 0
fi

{
    echo "=== zero-perm subtitle detection: $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    echo "New affected files: ${#new_files[@]}"
    for f in "${new_files[@]}"; do
        echo "--- $f ---"
        stat "$f" 2>&1
        echo "$f" >> "$STATE_FILE"
    done
    echo "--- mount info for /mnt/media ---"
    df -h /mnt/media 2>&1
    mount | grep -F "/mnt/media" 2>&1
    echo "--- containers currently running or recently exited ---"
    docker ps -a --format '{{.Names}}\t{{.Status}}' 2>&1
    echo "=== end detection ==="
    echo ""
} >> "$FORENSIC_LOG"

find /mnt/media -iname "*.srt" -not -perm -u+r -print0 2>/dev/null | xargs -0 --no-run-if-empty chmod 644

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) fixed ${#new_files[@]} newly-detected zero-perm subtitle file(s), forensics captured in $FORENSIC_LOG" >> /home/jay/logs/zero-perm-subtitle-monitor.log
