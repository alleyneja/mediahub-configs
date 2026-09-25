#!/bin/bash
# Daily fleet inventory (2026-09-25): writes INVENTORY.md in the PRIVATE alleyneja/mediahub-issues repo (checkout at
# ~/mediahub-issues) and pushes it, so git history shows exactly when any OS/kernel/Docker/container version changed.
# Cron on production: 45 6 * * *  (after the morning unattended-upgrades run). Never write this output to a public repo.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib-fleet-facts.sh"
REPO=/home/jay/mediahub-issues
OUT="$REPO/INVENTORY.md"
{
  echo "# Fleet inventory"
  echo
  echo "_Generated $(date '+%F %H:%M %Z') by \`mediahub-configs/scripts/fleet-inventory.sh\`. Private: exact versions."
  echo "Git history of this file = when anything changed. Issues snapshot their own versions with \`issue-env.sh\`._"
  for m in "${FLEET_ORDER[@]}"; do
    echo; echo "## $m"; echo
    if ! fleet_run "$m" true 2>/dev/null; then echo "_unreachable at generation time_"; continue; fi
    fleet_sys_md "$m" | grep -v "^- \*\*uptime:"; echo; fleet_ctr_md "$m"
  done
  echo; echo "## nas"; echo; echo "- UGREEN DH4300 Plus, UGOS (version: read from Control Panel > About; NAS SSH is normally closed)"
} > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
cd "$REPO" || exit 1
git pull -q --rebase 2>/dev/null
git add INVENTORY.md
# Only the "Generated" timestamp changed -> don't commit noise.
if git diff --cached --numstat INVENTORY.md | awk '{exit !($1<=1 && $2<=1)}'; then git reset -q INVENTORY.md; git checkout -q INVENTORY.md 2>/dev/null; exit 0; fi
git commit -q -m "inventory: $(date +%F)" && git push -q
