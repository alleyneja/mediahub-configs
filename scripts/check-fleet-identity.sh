#!/bin/bash
# Audits every stacks/*/docker-compose.yml for PUID/PGID/user identity declarations.
# Expected identity fleet-wide is 1000:1000 (jay) - see Global Constraints in the plan.
#
# IMPORTANT LIMITATION: this can only check stacks that DECLARE an identity. A stack
# with no PUID/PGID/user line at all runs as whatever the image default is (often
# root) - that is reported separately below as "declare no identity" and is NOT
# proof that stack is compliant. (Immich is exactly this case: no PUID/PGID/user in
# its compose file, and it has been observed writing root:root files into the pool -
# see docs/permissions-monitor.md.) Only a genuinely CONFLICTING declared value
# (PUID/PGID/user set to something other than 1000:1000) causes a nonzero exit.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/stacks"
EXPECTED_UID=1000
EXPECTED_GID=1000
drift_found=0
declared_count=0
undeclared_stacks=()

while IFS= read -r -d '' compose_file; do
    stack_name=$(basename "$(dirname "$compose_file")")
    stack_declares_identity=0

    while IFS= read -r line; do
        stack_declares_identity=1
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

    # Matches any "user:" line with a value, quoted or not, numeric pair or a named
    # form (e.g. `user: "1000:1000"`, `user: 0:0`, `user: root`). The previous
    # version only matched the quoted numeric-pair form, so `user: root` or
    # `user: 0:0` silently passed as compliant.
    while IFS= read -r line; do
        stack_declares_identity=1
        raw=$(echo "$line" | sed -E 's/^\s*user:\s*//' | tr -d '"' | xargs)
        if [ "$raw" != "${EXPECTED_UID}:${EXPECTED_GID}" ]; then
            echo "DRIFT: $stack_name sets user: \"$raw\" (expected ${EXPECTED_UID}:${EXPECTED_GID}) in $compose_file"
            drift_found=1
        fi
    done < <(grep -E '^\s*user:\s*\S+' "$compose_file" 2>/dev/null)

    if [ "$stack_declares_identity" -eq 1 ]; then
        declared_count=$((declared_count + 1))
    else
        undeclared_stacks+=("$stack_name")
    fi
done < <(find "$STACKS_DIR" -maxdepth 2 -iname "docker-compose.yml" -print0)

total_stacks=$(( declared_count + ${#undeclared_stacks[@]} ))

echo ""
if [ "$drift_found" -eq 0 ]; then
    echo "No CONFLICTING identity declarations: $declared_count/$total_stacks stack(s) that declare PUID/PGID/user all use ${EXPECTED_UID}:${EXPECTED_GID}."
else
    echo "CONFLICTING identity declaration(s) found - see DRIFT line(s) above."
fi

if [ "${#undeclared_stacks[@]}" -gt 0 ]; then
    echo "NOTE (not proof of compliance): ${#undeclared_stacks[@]}/$total_stacks stack(s) declare NO identity at all (run as image default, which may be root):"
    printf '  %s\n' "${undeclared_stacks[@]}"
fi

exit "$drift_found"
