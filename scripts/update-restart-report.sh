#!/bin/bash
# After each daily unattended-upgrades run, post to Discord which services the updates restarted, and which ones are
# waiting for the maintenance window (needrestart holds, production only). Runs as root from an ExecStopPost drop-in on
# apt-daily-upgrade.service (system/apt-daily-upgrade-restart-report.conf). docs/os-updates.md section F,
# alleyneja/mediahub-issues#53.
# Posts only when something was restarted, or when the waiting list gained a service since the last post, so a quiet
# day or a service still waiting from yesterday doesn't repeat the same message.
#   update-restart-report.sh [--dry-run] [--since "<date>"]   (--since: report on an earlier run, for testing)
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/home/jay/logs/update-restart-report.log
STATE=/home/jay/logs/fleet-health/deferred-restarts          # waiting list at the last post
HOST="$(hostname)"; TS="$(date '+%F %T %Z')"
# shellcheck disable=SC1090
[ -f "$SCRIPT_DIR/fleet-healthcheck.env" ] && . "$SCRIPT_DIR/fleet-healthcheck.env"
post(){ [ -n "${FLEET_HEALTH_WEBHOOK:-}" ] || return 0
  python3 -c 'import json,sys; print(json.dumps({"username":"Fleet Health","content":sys.argv[1][:1900]}))' "$1" \
    | curl -s -m 10 -H 'Content-Type: application/json' -d @- "$FLEET_HEALTH_WEBHOOK" >/dev/null; }

DRY=0; SINCE=""
while [ $# -gt 0 ]; do case "$1" in --dry-run) DRY=1;; --since) SINCE="$2"; shift;; esac; shift; done
[ -n "$SINCE" ] || SINCE="$(systemctl show apt-daily-upgrade.service -p ExecMainStartTimestamp --value)"
start=$(date -d "$SINCE" +%s 2>/dev/null) || { echo "$TS host=$HOST cannot parse start time '$SINCE'" >> "$LOG"; exit 0; }

# --- packages this run installed or upgraded (apt history, transactions that started at/after the run began) --------
pkgs=$(python3 - "$start" <<'PY'
import re, sys, datetime
start = int(sys.argv[1]); out = []; take = False
for line in open('/var/log/apt/history.log', errors='replace'):
    if line.startswith('Start-Date:'):
        t = datetime.datetime.strptime(line.split(':', 1)[1].strip(), '%Y-%m-%d  %H:%M:%S')
        take = t.timestamp() >= start - 5
    elif take and line.startswith(('Install:', 'Upgrade:')):
        out += re.findall(r'(?:^|\), )([^:\s]+):', line.split(':', 1)[1].strip())
print(' '.join(dict.fromkeys(out)))
PY
)
npkgs=$(wc -w <<<"$pkgs")

# --- services pid 1 stopped during the run (needrestart restarts + packages restarting their own daemon) ------------
# (-t systemd + "systemd[1]:" rather than _PID=1: when journald itself is restarted, pid 1's lines arrive via kmsg
#  with no _PID, and those are exactly the seconds that matter - tailscaled was missed that way on 2026-09-26)
restarted=$(journalctl -q --no-pager -o short -t systemd --since "@$start" 2>/dev/null \
  | sed -nE 's/.* systemd\[1\]: Stopping ([^ ]+)\.service - .*/\1/p' \
  | grep -vE '^(apt-daily-upgrade|unattended-upgrades|user@[0-9]+|session-.*|packagekit|fwupd|man-db|motd-news|systemd-tmpfiles-clean)$' \
  | sort -u | tr '\n' ' ' | sed 's/ $//')

# --- services needrestart is holding for the maintenance window ----------------------------------------------------
waiting=""
if command -v needrestart >/dev/null 2>&1; then
  waiting=$(needrestart -b -r l 2>/dev/null | sed -nE 's/^NEEDRESTART-SVC: (.+)\.service$/\1/p' \
    | grep -E '^(tailscaled|xorg-headless|openbox-headless|sunshine)$' | sort | tr '\n' ' ' | sed 's/ $//')
fi
new_waiting=$(comm -13 <(tr ' ' '\n' 2>/dev/null < "$STATE" | sort) <(tr ' ' '\n' <<<"$waiting" | sort) | grep -v '^$' | tr '\n' ' ')

echo "$TS host=$HOST since=@$start pkgs=$npkgs restarted=[$restarted] waiting=[$waiting] new_waiting=[${new_waiting% }]" >> "$LOG"
[ "$DRY" = 0 ] && echo "$waiting" > "$STATE"
[ -z "$restarted" ] && [ -z "${new_waiting// }" ] && { [ "$DRY" = 1 ] && echo "DRY: nothing to report"; exit 0; }

case "$HOST" in mediahub-production) win=Tue;; mediahub-r9) win=Thu;; *) win=Sat;; esac
msg="🔁 **$HOST** daily updates ($npkgs package(s)): "
if [ -n "$restarted" ]; then msg+="restarted \`${restarted// /\`, \`}\`"; else msg+="no services restarted"; fi
[[ " $restarted " == *" tailscaled "* ]] && msg+=$'\n'"⚠️ tailscaled restarted: open Tailscale SSH sessions to this machine were dropped."
[[ " $restarted " =~ \ (sunshine|xorg-headless)\  ]] && msg+=$'\n'"⚠️ Sunshine display stack restarted: any Moonlight stream was dropped."
[ -n "$waiting" ] && msg+=$'\n'"⏸️ Waiting for the $win maintenance window: \`${waiting// /\`, \`}\`"
[ -n "$pkgs" ] && msg+=$'\n'"-# packages: ${pkgs:0:1200}"
if [ "$DRY" = 1 ]; then echo "DRY: would post:"; echo "$msg"; else post "$msg"; fi
