#!/bin/bash
# Environment snapshot for a problem issue (2026-09-25). Prints markdown for the "Environment at the time" field.
#   issue-env.sh <machine> [container-name-regex]     e.g.  issue-env.sh r9 'plex'   issue-env.sh production 'nextcloud.*'
# Machines: production, r9, staging. Paste the output ONLY into the private mediahub-issues repo.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib-fleet-facts.sh"
m="${1:?usage: issue-env.sh <production|r9|staging> [container-regex]}"
[ -n "${FLEET_HOSTS[$m]:-}" ] || { echo "unknown machine: $m" >&2; exit 1; }
echo "**$m** at $(date '+%F %H:%M %Z')"
echo
fleet_sys_md "$m"
if [ -n "${2:-}" ]; then echo; fleet_ctr_md "$m" "$2"; fi
