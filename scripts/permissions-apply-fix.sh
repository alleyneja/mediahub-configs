#!/bin/bash
# Applies the fix for files the permissions monitor queued. Run this AFTER reviewing
# /home/jay/logs/permissions-forensics.log - it is the only script in this family that
# ever runs chmod. Run with no arguments to fix everything currently queued, or pass
# specific paths to fix only those (leaving the rest queued).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PENDING_FILE="/home/jay/logs/permissions-pending-fixes.txt"
FORENSIC_LOG="/home/jay/logs/permissions-forensics.log"

# shellcheck disable=SC1090
source "$SCRIPT_DIR/lib-permissions-alert.sh"

touch "$PENDING_FILE"

if [ "$#" -gt 0 ]; then
    to_fix=("$@")
else
    mapfile -t to_fix < "$PENDING_FILE"
fi

if [ ${#to_fix[@]} -eq 0 ]; then
    echo "Nothing queued."
    exit 0
fi

fixed=()
for f in "${to_fix[@]}"; do
    [ -z "$f" ] && continue
    if [ -f "$f" ]; then
        chmod 644 "$f"
        fixed+=("$f")
    else
        echo "Skipping (no longer exists): $f"
    fi
done

if [ ${#fixed[@]} -eq 0 ]; then
    echo "Nothing fixed."
    exit 0
fi

# Rewrite the pending file, dropping only what we just fixed
tmp_file=$(mktemp)
while IFS= read -r line; do
    keep=1
    for f in "${fixed[@]}"; do
        [ "$line" = "$f" ] && keep=0 && break
    done
    [ "$keep" -eq 1 ] && echo "$line" >> "$tmp_file"
done < "$PENDING_FILE"
mv "$tmp_file" "$PENDING_FILE"

{
    echo "=== fix applied: $(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname) ==="
    printf '%s\n' "${fixed[@]}"
    echo "=== end fix ==="
    echo ""
} >> "$FORENSIC_LOG"

sample=$(printf '%s\n' "${fixed[@]:0:5}")
send_discord_alert "Resolved: chmod 644 applied to ${#fixed[@]} file(s) on **$(hostname)**.
$sample"

echo "Fixed ${#fixed[@]} file(s)."
