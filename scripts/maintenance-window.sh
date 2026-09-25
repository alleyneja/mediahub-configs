#!/bin/bash
# Per-machine maintenance window (docs/os-updates.md, section C). Run from ROOT's crontab every 30 min inside the
# machine's window (production Tue, r9 Thu, staging Sat, 02:00-04:30):
#   0,30 2-4 * * <dow> /path/maintenance-window.sh
# Each run: if this machine already rebooted in tonight's window, do nothing. Otherwise apply ALL pending updates
# (including Docker and the NVIDIA driver, which the daily unattended-upgrades deliberately skips) and reboot ONLY if
# a reboot is required - and only if nobody is streaming on Plex. If someone is watching, try again next half hour;
# after the window closes, next week. apt-mark holds (e.g. sunshine on production) are respected by apt.
# After the reboot, fleet-healthcheck.service verifies the machine and reports to Discord.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG=/home/jay/logs/maintenance-window.log
STATE=/home/jay/logs/fleet-health/maintenance-last-reboot   # date (YYYY-MM-DD) of last window reboot
HOST="$(hostname)"; TODAY="$(date +%F)"; TS="$(date '+%F %T %Z')"
# shellcheck disable=SC1090
[ -f "$SCRIPT_DIR/fleet-healthcheck.env" ] && . "$SCRIPT_DIR/fleet-healthcheck.env"
log(){ echo "$TS host=$HOST $*" >> "$LOG"; }
post(){ [ -n "${FLEET_HEALTH_WEBHOOK:-}" ] || return 0
  python3 -c 'import json,sys; print(json.dumps({"username":"Fleet Health","content":sys.argv[1][:1900]}))' "$1" \
    | curl -s -m 10 -H 'Content-Type: application/json' -d @- "$FLEET_HEALTH_WEBHOOK" >/dev/null; }

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
[ "$(cat "$STATE" 2>/dev/null)" = "$TODAY" ] && exit 0      # already rebooted in tonight's window

# --- is anyone watching Plex? (production and r9 only: Plex runs on r9 and depends on production for Live TV and the
#     prod-internal media branch; staging reboots don't touch Plex). Prints a count, or "unknown".
plex_streams(){
  local q='T=$(sudo -n grep -o "PlexOnlineToken=\"[^\"]*\"" "/srv/docker/plex/Library/Application Support/Plex Media Server/Preferences.xml" | cut -d\" -f2); curl -s -m 10 -H "Accept: application/json" "http://127.0.0.1:32400/status/sessions?X-Plex-Token=$T" | python3 -c "import json,sys; print(json.load(sys.stdin)[\"MediaContainer\"].get(\"size\",0))"'
  local n
  if [ "$HOST" = mediahub-r9 ]; then n=$(sudo -u jay bash -c "$q" 2>/dev/null)
  else n=$(sudo -u jay ssh -o BatchMode=yes -o ConnectTimeout=5 jay@192.168.0.22 "$q" 2>/dev/null); fi
  if [[ "$n" =~ ^[0-9]+$ ]]; then echo "$n"
  elif [ "$HOST" != mediahub-r9 ] && ! ping -c1 -W2 192.168.0.22 >/dev/null 2>&1; then echo 0   # r9 down: nobody can be watching
  else echo unknown; fi
}

DRY=0; [ "${1:-}" = --dry-run ] && DRY=1
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1
# NVIDIA driver packages are held and NEVER upgraded automatically: Ubuntu retires driver branches by turning the old
# branch's packages into transitional ones that pull in the next branch (seen 2026-09-25: 535 -> 580 on staging), so an
# "update" can be a partial driver switch. Pending driver updates are reported for a deliberate upgrade instead.
NV_RE='^(nvidia-(driver|dkms|kernel-common|kernel-source|utils|compute-utils|firmware|headless)-[0-9]|libnvidia-(cfg1|common|compute|decode|encode|extra|fbc1|gl)-[0-9]|xserver-xorg-video-nvidia-[0-9])'
mapfile -t nv_pkgs < <(dpkg-query -W -f '${Package} ${db:Status-Abbrev}\n' 2>/dev/null | awk '$2 ~ /^[ih]i/ {print $1}' | grep -E "$NV_RE")
[ "$DRY" = 0 ] && [ "${#nv_pkgs[@]}" -gt 0 ] && apt-mark hold "${nv_pkgs[@]}" >/dev/null 2>&1
nv_pending=$(apt list --upgradable 2>/dev/null | cut -d/ -f1 | grep -E "$NV_RE" | tr '\n' ' ')
pending=$(apt-get -s upgrade --with-new-pkgs 2>/dev/null | awk '/^Inst /{print $2}' | grep -vE "$NV_RE|^(nvidia|libnvidia)-.*-[0-9]{3}$" | tr '\n' ' ')
reboot_needed=0; [ -f /var/run/reboot-required ] && reboot_needed=1
# a kernel, Docker or NVIDIA update in the pending set will need a restart of the machine or of every container
echo "$pending" | grep -qE '(^| )(linux-image|linux-modules|docker-ce|containerd\.io|nvidia-container)' && reboot_needed=1
why="$(tr '\n' ' ' 2>/dev/null < /var/run/reboot-required.pkgs)$(echo "$pending" | grep -oE '(^| )(linux-image[^ ]*|docker-ce|containerd\.io|nvidia-driver[^ ]*)' | tr -d '\n')"

if [ -n "$nv_pending" ] && [ "$DRY" = 0 ] && [ "$(date +%H)" = 02 ] && [ "$(date +%M)" -lt 15 ]; then
  log "NVIDIA driver updates held for deliberate upgrade: $nv_pending"
  post "🟡 **$HOST**: NVIDIA driver updates are available but held on purpose (they can switch driver branches). Upgrade deliberately: $nv_pending"
fi

# --- nobody may be watching before ANYTHING disruptive (a Docker upgrade alone restarts every container) ---------
if [ "$reboot_needed" = 1 ]; then
  case "$HOST" in
    mediahub-production|mediahub-r9)
      s=$(plex_streams)
      if [ "$s" != 0 ]; then
        log "reboot needed ($why) but Plex streams=$s - deferring"
        [ "$DRY" = 1 ] && { echo "DRY: would defer (Plex streams=$s)"; exit 0; }
        [ "$(date +%H%M)" -ge 0430 ] && post "⏸️ **$HOST** needs a reboot ($why) but Plex was busy (${s} stream(s)) all window - deferred to next week. Reboot by hand if convenient."
        exit 0
      fi ;;
  esac
fi
if [ "$DRY" = 1 ]; then
  echo "DRY: held-nvidia-updates=[${nv_pending:-none}] pending=[${pending:-none}] reboot_needed=$reboot_needed why=[${why}] plex_streams=$( [ "$HOST" = mediahub-staging ] && echo n/a || plex_streams)"; exit 0
fi

# --- apply updates (only packages that don't need a restart are handled here when no reboot is due) ---------------
if [ -n "$pending" ]; then
  log "upgrading: $pending"
  if ! apt-get -y -q -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade --with-new-pkgs >> "$LOG" 2>&1; then
    log "ERROR: apt upgrade failed"; post "❌ **$HOST** maintenance window: package upgrade FAILED - see $LOG"; exit 1
  fi
fi
if [ "$reboot_needed" = 0 ] && [ -f /var/run/reboot-required ]; then
  # an ordinary update turned out to need a reboot: the Plex check hasn't run yet, so run it now
  reboot_needed=1; why="$(tr '\n' ' ' 2>/dev/null < /var/run/reboot-required.pkgs)"
  case "$HOST" in mediahub-production|mediahub-r9)
    s=$(plex_streams); [ "$s" != 0 ] && { log "updates applied; reboot now required ($why) but Plex streams=$s - deferring"; exit 0; } ;;
  esac
fi
if [ "$reboot_needed" = 0 ]; then
  [ -n "$pending" ] && log "updates applied, no reboot required"
  exit 0
fi
log "rebooting: required by $why"
echo "$TODAY" > "$STATE"
post "🔄 **$HOST** maintenance window: rebooting (required by: ${why:-kernel/system update}). Health report follows in ~4 min."
sleep 5
systemctl reboot
