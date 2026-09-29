#!/bin/bash
# Fleet-wide permissions forensic monitor. Replaces zero-perm-subtitle-monitor.sh
# (subtitles only) with whole-pool coverage of both known anomaly patterns: mode 000
# (unreadable, even by owner) and mode 777 (unexpectedly permissive). See D12 in
# the private tracker alleyneja/mediahub-issues #33 for the investigation this supports.
#
# ALERT-FIRST, NEVER AUTO-FIX: this script only detects, logs, and queues. It never
# runs chmod. Run permissions-apply-fix.sh after reviewing the queue to actually fix.
#
# Alert volume control: below BURST_THRESHOLD new files in a single run, this logs
# and queues as always but does NOT send an immediate Discord alert - it appends a
# one-line summary to DIGEST_FILE for permissions-digest-send.sh to roll up once a
# day. At/above BURST_THRESHOLD, it alerts immediately (this is the incident-signature
# case, e.g. the Sep 20 burst). See docs/permissions-monitor.md.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_TAG="$(hostname)"
MEDIA_ROOT="/mnt/media"
STATE_FILE="/home/jay/logs/permissions-monitor-seen.txt"
FORENSIC_LOG="/home/jay/logs/permissions-forensics.log"
PENDING_FILE="/home/jay/logs/permissions-pending-fixes.txt"
DIGEST_FILE="/home/jay/logs/permissions-digest-pending.txt"
LOCK_FILE="/tmp/permissions-monitor.lock"
# Observed routine noise (mostly 777-on-creation writes) has been single digits to
# ~10/run; real incidents in this system's history (e.g. the Sep 20 burst) have been
# in the hundreds-to-thousands. 50 sits well above routine noise, well below a real
# incident signature.
BURST_THRESHOLD=50

mkdir -p /home/jay/logs
touch "$STATE_FILE" "$PENDING_FILE" "$DIGEST_FILE"

# Prevent overlapping runs: cron fires every 15 min, but a slow `find` over r9's
# NFS-backed pool (or a future slow host) could still be running past the next tick.
# Two overlapping runs would both see the same "new" files and could double-alert.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$HOST_TAG: skipped, previous run still holds the lock" >> "$FORENSIC_LOG"
    exit 0
fi

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Without this check, an unmounted /mnt/media produces zero find results and a
# silent, "successful" exit - the guardrail goes blind with no signal at all. Page on
# this explicitly instead.
if ! mountpoint -q "$MEDIA_ROOT"; then
    {
        echo "=== permissions-monitor ALERT: $MEDIA_ROOT NOT MOUNTED: $TS host=$HOST_TAG ==="
        echo ""
    } >> "$FORENSIC_LOG"
    send_discord_alert "permissions-monitor on **$HOST_TAG**: $MEDIA_ROOT is NOT MOUNTED. Scan skipped this run - the guardrail is blind until this is fixed."
    exit 1
fi

# .permissions-canary is excluded: permissions-canary.sh keeps an unfixed control half there that must never be
# looked up by name (a by-name lookup heals the NAS-side state the canary exists to observe).
CANARY_PRUNE=(-path "$MEDIA_ROOT/.permissions-canary" -prune -o)
# Record the mode find actually matched on (%m): the stat below runs seconds-to-minutes later and can show a different,
# healthy mode (2026-09-26: 12 .es.srt files flagged mid-write read as 0640 at stat time; alleyneja/mediahub-issues#33).
mapfile -d '' -t raw_000 < <(find "$MEDIA_ROOT" "${CANARY_PRUNE[@]}" -type f -not -perm -u+r -printf '%m %p\0' 2>/dev/null)
declare -A find_mode; bad_000=()
for e in "${raw_000[@]}"; do find_mode["${e#* }"]="${e%% *}"; bad_000+=("${e#* }"); done
mapfile -d '' -t bad_777 < <(find "$MEDIA_ROOT" "${CANARY_PRUNE[@]}" -type f -perm 777 -print0 2>/dev/null)
total_scanned=$(( ${#bad_000[@]} + ${#bad_777[@]} ))

# Load the dedup state once into an associative array instead of running grep -qxF
# per candidate against a 9,300+ line (and growing) file - that was O(n*m) and
# already measured at ~29s/pass before find's own traversal time. This is O(n+m).
declare -A seen
while IFS= read -r line; do
    [ -n "$line" ] && seen["$line"]=1
done < "$STATE_FILE"

new_files=()
for f in "${bad_000[@]}" "${bad_777[@]}"; do
    if [ -z "${seen[$f]+x}" ]; then
        new_files+=("$f")
    fi
done

if [ ${#new_files[@]} -eq 0 ]; then
    # Distinguishes "ran and found nothing new" from "never ran" without needing a
    # full forensic block every 15 minutes.
    echo "$TS host=$HOST_TAG scanned=$total_scanned new=0" >> "$FORENSIC_LOG"
    exit 0
fi

{
    echo "=== permissions anomaly detection: $TS host=$HOST_TAG ==="
    echo "New affected files: ${#new_files[@]} (scanned $total_scanned total flagged-mode files this run)"
    for f in "${new_files[@]}"; do
        echo "--- $f ---"
        stat "$f" 2>&1
        branch=$(getfattr --only-values -n user.mergerfs.basepath "$f" 2>/dev/null)
        echo "mergerfs branch: ${branch:-unknown}"
        echo "$f" >> "$STATE_FILE"
        seen_mode=${find_mode[$f]:-}
        now_mode=$(stat -c %a "$f" 2>/dev/null)
        if [ -n "$seen_mode" ]; then
            echo "mode seen by find: $seen_mode"
            if [ -n "$now_mode" ] && [ $(( 8#$now_mode & 8#400 )) -ne 0 ]; then
                # owner-readable again already: nothing to fix, and a chmod would overwrite the writer's intended mode
                echo "TRANSIENT: owner-unreadable ($seen_mode) when find saw it, $now_mode at stat time - not queued for fix"
                continue
            fi
        fi
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

# Only mode 000 (the ugacl defect: unreadable) can page immediately. Mode 777 is the harmless
# NAS-ugacl-ignores-umask-on-create half (runbook), and a bulk podcast/library download
# tripped the burst rule on 2026-09-28 (51-225 files/run, all 777 jay:jay) - those go to the digest.
new_000=()
for f in "${new_files[@]}"; do
    [ -n "${find_mode[$f]+x}" ] && new_000+=("$f")
done

if [ ${#new_000[@]} -ge "$BURST_THRESHOLD" ]; then
    sample=$(printf '%s\n' "${new_000[@]:0:5}")
    if ! send_discord_alert "Permissions anomaly on **$HOST_TAG**: ${#new_000[@]} new mode-000 file(s) (>= burst threshold $BURST_THRESHOLD - alerting immediately; ${#new_files[@]} new flagged files total incl. mode 777).
Sample:
$sample
Full detail in $FORENSIC_LOG on $HOST_TAG. NOT auto-fixed - review then run permissions-apply-fix.sh."; then
        echo "$TS host=$HOST_TAG WARNING: burst Discord alert failed to send for ${#new_files[@]} new files - they were still logged to $FORENSIC_LOG and queued to $PENDING_FILE above, just no notification went out" >> "$FORENSIC_LOG"
    fi
else
    # Below threshold, or mode-777-only: routine noise. Full detail already went to the forensics log
    # and PENDING_FILE above - this only defers the immediate Discord ping in favor
    # of permissions-digest-send.sh's once-daily rollup.
    echo "$TS host=$HOST_TAG count=${#new_files[@]}" >> "$DIGEST_FILE"
fi
