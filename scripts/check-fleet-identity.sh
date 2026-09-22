#!/bin/bash
# Audits every stacks/*/docker-compose.yml for PUID/PGID/user consistency.
# Expected identity fleet-wide is 1000:1000 (jay) - see Global Constraints in the plan.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/stacks"
EXPECTED_UID=1000
EXPECTED_GID=1000
drift_found=0

while IFS= read -r -d '' compose_file; do
    stack_name=$(basename "$(dirname "$compose_file")")

    while IFS= read -r line; do
        val=$(echo "$line" | grep -oE '[0-9]+' | head -1)
        if echo "$line" | grep -q "PUID" && [ "$val" != "$EXPECTED_UID" ]; then
            echo "DRIFT: $stack_name sets PUID=$val (expected $EXPECTED_UID) in $compose_file"
            drift_found=1
        fi
        if echo "$line" | grep -q "PGID" && [ "$val" != "$EXPECTED_GID" ]; then
            echo "DRIFT: $stack_name sets PGID=$val (expected $EXPECTED_GID) in $compose_file"
            drift_found=1
        fi
    done < <(grep -E 'PUID|PGID' "$compose_file" 2>/dev/null)

    while IFS= read -r line; do
        pair=$(echo "$line" | grep -oE '[0-9]+:[0-9]+')
        if [ "$pair" != "${EXPECTED_UID}:${EXPECTED_GID}" ]; then
            echo "DRIFT: $stack_name sets user: \"$pair\" (expected ${EXPECTED_UID}:${EXPECTED_GID}) in $compose_file"
            drift_found=1
        fi
    done < <(grep -E '^\s*user:\s*"[0-9]+:[0-9]+"' "$compose_file" 2>/dev/null)
done < <(find "$STACKS_DIR" -maxdepth 2 -iname "docker-compose.yml" -print0)

if [ "$drift_found" -eq 0 ]; then
    echo "OK: all stacks under $STACKS_DIR consistently use ${EXPECTED_UID}:${EXPECTED_GID}"
    exit 0
fi
exit 1
