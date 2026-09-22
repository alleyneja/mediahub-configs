#!/bin/bash
# Applies the fix for files the permissions monitor queued. Run this AFTER reviewing
# /home/jay/logs/permissions-forensics.log - it is the only script in this family that
# ever runs chmod. Pass specific path(s) to fix only those (leaving the rest queued).
# To fix EVERYTHING currently queued, you must pass --all explicitly - a bare
# invocation with no arguments now refuses, on purpose (see docs/permissions-monitor.md
# for why: most of the live queue is root-owned and this used to silently "fix" those
# by doing nothing but marking them done anyway).
#
# chmod's exit status is checked per file. A file this process doesn't own (e.g. the
# ~84% of the live queue that's root:root, chmod'd by unprivileged jay with no sudo)
# fails with EPERM and is now left in the pending queue and reported separately as
# "failed" - it is never silently marked fixed.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PENDING_FILE="/home/jay/logs/permissions-pending-fixes.txt"
FORENSIC_LOG="/home/jay/logs/permissions-forensics.log"
STATE_FILE="/home/jay/logs/permissions-monitor-seen.txt"
MEDIA_ROOT="/mnt/media"

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

touch "$PENDING_FILE"

all_mode=0
args=()
for a in "$@"; do
    if [ "$a" = "--all" ]; then
        all_mode=1
    else
        args+=("$a")
    fi
done

if [ "${#args[@]}" -gt 0 ]; then
    to_fix=("${args[@]}")
elif [ "$all_mode" -eq 1 ]; then
    mapfile -t to_fix < "$PENDING_FILE"
else
    echo "Refusing: no path given and --all not passed. This would otherwise silently" >&2
    echo "attempt to fix EVERYTHING in $PENDING_FILE." >&2
    echo "Pass explicit path(s) to fix only those, or pass --all to deliberately fix" >&2
    echo "the entire queue (read docs/permissions-monitor.md first)." >&2
    exit 2
fi

if [ ${#to_fix[@]} -eq 0 ]; then
    echo "Nothing queued."
    exit 0
fi

fixed=()
failed=()
outside_root=()

for f in "${to_fix[@]}"; do
    [ -z "$f" ] && continue

    case "$f" in
        "$MEDIA_ROOT"/*) ;;
        *)
            echo "Refusing (outside $MEDIA_ROOT): $f" >&2
            outside_root+=("$f")
            continue
            ;;
    esac

    if [ ! -f "$f" ]; then
        echo "Skipping (no longer exists): $f"
        continue
    fi

    if chmod 644 "$f" 2>>"$FORENSIC_LOG"; then
        fixed+=("$f")
    else
        echo "FAILED chmod (left queued): $f" >&2
        failed+=("$f")
    fi
done

if [ ${#fixed[@]} -eq 0 ] && [ ${#failed[@]} -eq 0 ]; then
    echo "Nothing fixed. (${#outside_root[@]} rejected as outside $MEDIA_ROOT.)"
    exit 0
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Rewrite the pending file in a single pass, dropping only what was actually fixed.
# Failed and outside-root paths stay queued - they were never removed from to_fix's
# source in the first place unless they're in `fixed`.
if [ ${#fixed[@]} -gt 0 ]; then
    declare -A fixed_lookup
    for f in "${fixed[@]}"; do fixed_lookup["$f"]=1; done

    tmp_file=$(mktemp "${PENDING_FILE}.tmp.XXXXXX")
    while IFS= read -r line; do
        [ -n "${fixed_lookup[$line]+x}" ] || echo "$line" >> "$tmp_file"
    done < "$PENDING_FILE"
    mv "$tmp_file" "$PENDING_FILE"

    # Also clear successfully-fixed files out of the monitor's dedup state file. If
    # we don't, and one of these genuinely flips again later - the exact recurring
    # pattern this whole project exists to catch - the monitor will treat it as
    # "already seen" and never re-alert on it, forever.
    if [ -f "$STATE_FILE" ]; then
        tmp_state=$(mktemp "${STATE_FILE}.tmp.XXXXXX")
        while IFS= read -r line; do
            [ -n "${fixed_lookup[$line]+x}" ] || echo "$line" >> "$tmp_state"
        done < "$STATE_FILE"
        mv "$tmp_state" "$STATE_FILE"
    fi
fi

{
    echo "=== fix applied: $TS host=$(hostname) ==="
    if [ ${#fixed[@]} -gt 0 ]; then
        echo "Fixed (chmod 644 succeeded, cleared from pending queue and seen-state):"
        printf '  %s\n' "${fixed[@]}"
    fi
    if [ ${#failed[@]} -gt 0 ]; then
        echo "FAILED (chmod exited nonzero - left in pending queue, likely not owned by jay):"
        printf '  %s\n' "${failed[@]}"
    fi
    echo "=== end fix ==="
    echo ""
} >> "$FORENSIC_LOG"

msg="Permissions fix run on **$(hostname)**: resolved ${#fixed[@]}, failed ${#failed[@]}."
if [ ${#failed[@]} -gt 0 ]; then
    msg="$msg Failed ones are still queued - see $FORENSIC_LOG for which."
fi
sample_fixed=$(printf '%s\n' "${fixed[@]:0:5}")
send_discord_alert "$msg
Sample fixed:
$sample_fixed"

echo "Fixed ${#fixed[@]} file(s). Failed ${#failed[@]} file(s) (left queued)."
