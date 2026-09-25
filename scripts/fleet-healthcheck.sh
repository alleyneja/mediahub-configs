#!/bin/bash
# Fleet health check (2026-09-25; OS-update decision in docs/os-updates.md). Runs on production, r9 and staging.
#   fleet-healthcheck.sh --boot   after every boot (systemd unit fleet-healthcheck.service): always posts a report
#   fleet-healthcheck.sh          every 30 min (cron): posts only when the set of problems changes
#   fleet-healthcheck.sh --test   run now and always post (for checking Discord delivery)
# Checks: required mounts (and the D12 lookupcache=none fix), containers that were running before a reboot and aren't
# now, running containers with no network (the Sep 23 Threadfin failure), crash-looping/unhealthy containers, and a
# per-host list of service endpoints. Auto-fixes ONLY known-safe cases: mount a missing fstab mount, start a container
# that was running before the reboot, recreate a container that lost its network. Everything else is report-only.
# Containers stopped on purpose are never touched: "expected running" is a snapshot of what was actually running,
# refreshed every run while the machine stays up. Containers with restart policy "no" (Pterodactyl game servers,
# managed by Wings) are ignored.
set -u
MODE="${1:-periodic}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE=/home/jay/logs/fleet-health; mkdir -p "$STATE"
LOG="$STATE/fleet-health.log"; SNAP="$STATE/expected-running.txt"; LAST="$STATE/last-problems.txt"
HOST="$(hostname)"; TS="$(date '+%Y-%m-%d %H:%M:%S %Z')"
ENV_FILE="$SCRIPT_DIR/fleet-healthcheck.env"   # FLEET_HEALTH_WEBHOOK=... (gitignored)
# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && . "$ENV_FILE"

problems=(); fixes=()
bad(){ problems+=("$1"); }
fixed(){ fixes+=("$1"); }
http_ok(){ curl -s -o /dev/null -m 8 -w '%{http_code}' "$1" 2>/dev/null | grep -qE '^(200|204|301|302|401)$'; }

# ---- per-host configuration -------------------------------------------------------------------------------------
case "$HOST" in
  mediahub-production)
    MOUNTS=(/mnt/nas /mnt/media /mnt/permissions-canary-control); LOOKUPCACHE_MOUNT=/mnt/nas
    ENDPOINTS=("Threadfin|http://127.0.0.1:34400/discover.json" "Nextcloud|https://nextcloud.lan/status.php"
               "Uptime Kuma|http://127.0.0.1:3001" "Caddy|https://plex.lan") ;;
  mediahub-r9)
    MOUNTS=(/mnt/nas /mnt/prod-internal /mnt/media); LOOKUPCACHE_MOUNT=/mnt/nas
    ENDPOINTS=("Plex|http://127.0.0.1:32400/identity" "subgen (tailnet-only)|http://100.121.244.45:9000/") ;;
  mediahub-staging)
    MOUNTS=(); LOOKUPCACHE_MOUNT=""
    ENDPOINTS=("Home Assistant (tailnet-only)|http://100.124.234.117:8123/") ;;
  *) MOUNTS=(); LOOKUPCACHE_MOUNT=""; ENDPOINTS=() ;;
esac

# ---- 1. mounts --------------------------------------------------------------------------------------------------
for m in "${MOUNTS[@]}"; do
  if ! mountpoint -q "$m"; then
    if sudo -n mount "$m" 2>/dev/null && mountpoint -q "$m"; then fixed "mounted $m (was missing)"
    else bad "$m is NOT mounted (auto-mount failed)"; fi
  fi
done
if [ -n "$LOOKUPCACHE_MOUNT" ] && mountpoint -q "$LOOKUPCACHE_MOUNT"; then
  awk -v m="$LOOKUPCACHE_MOUNT" '$2==m{o=$4} END{print o}' /proc/mounts | grep -q lookupcache=none \
    || bad "$LOOKUPCACHE_MOUNT is mounted WITHOUT lookupcache=none (D12 NAS fix missing)"
fi

# ---- 2. AdGuard DNS answers (hosts that run it) -----------------------------------------------------------------
if docker ps --format '{{.Names}}' | grep -qx adguardhome; then
  dig +short +time=3 +tries=1 @127.0.0.1 example.com 2>/dev/null | grep -qE '^[0-9.]+$' || bad "AdGuard DNS on $HOST is not answering"
fi

# ---- 3. containers ----------------------------------------------------------------------------------------------
boot_epoch=$(( $(date +%s) - $(cut -d. -f1 /proc/uptime) ))
snap_epoch=$(stat -c %Y "$SNAP" 2>/dev/null || echo 0)
mapfile -t running < <(docker ps --format '{{.Names}}' | sort)
if [ -f "$SNAP" ] && [ "$snap_epoch" -lt "$boot_epoch" ]; then
  # First run since a reboot: anything that was running before and isn't now gets started.
  while IFS= read -r c; do
    [ -z "$c" ] && continue
    printf '%s\n' "${running[@]}" | grep -qx "$c" && continue
    if docker start "$c" >/dev/null 2>&1; then fixed "started $c (was running before the reboot, didn't come back)"
    else bad "$c was running before the reboot and could not be started"; fi
  done < "$SNAP"
  mapfile -t running < <(docker ps --format '{{.Names}}' | sort)
fi
for c in "${running[@]}"; do
  read -r nmode nnets policy proj svc wd < <(docker inspect -f '{{.HostConfig.NetworkMode}} {{len .NetworkSettings.Networks}} {{.HostConfig.RestartPolicy.Name}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}} {{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c" 2>/dev/null)
  case "$nmode" in
    host|none) ;;
    container:*)
      tgt="${nmode#container:}"
      [ "$(docker inspect -f '{{.State.Running}}' "$tgt" 2>/dev/null)" = true ] || bad "$c shares the network of a container that is gone or stopped" ;;
    *)
      if [ "$nnets" = 0 ]; then
        if [ -n "$wd" ] && [ -d "$wd" ] && (cd "$wd" && docker compose up -d --force-recreate --no-deps "$svc" >/dev/null 2>&1) \
           && [ "$(docker inspect -f '{{len .NetworkSettings.Networks}}' "$c" 2>/dev/null)" != 0 ]; then
          fixed "recreated $c (it was running with NO network attached)"
        else bad "$c is running with NO network attached (auto-recreate failed)"; fi
      fi ;;
  esac
done
while IFS=$'\t' read -r c st; do
  case "$st" in *Restarting*) bad "$c is crash-looping ($st)";; *unhealthy*) bad "$c is unhealthy";; esac
done < <(docker ps -a --format '{{.Names}}\t{{.Status}}')
# Refresh the snapshot of what should be running (restart policy "no" excluded: those are managed elsewhere).
docker ps -q | xargs -r docker inspect -f '{{.HostConfig.RestartPolicy.Name}} {{.Name}}' | awk '$1!="no"{sub("^/","",$2); print $2}' | sort > "$SNAP.tmp" && mv "$SNAP.tmp" "$SNAP"

# ---- 4. endpoints -----------------------------------------------------------------------------------------------
for e in "${ENDPOINTS[@]}"; do
  name="${e%%|*}"; url="${e#*|}"
  if [[ "$url" == https://*.lan* ]]; then
    h="${url#https://}"; h="${h%%/*}"
    code=$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve "$h:443:127.0.0.1" "$url")
    [[ "$code" =~ ^(200|204|301|302|401)$ ]] || bad "$name not answering ($url -> HTTP $code)"
  else http_ok "$url" || bad "$name not answering ($url)"; fi
done

# ---- 5. Plex Live TV tuner (r9) ---------------------------------------------------------------------------------
if [ "$HOST" = mediahub-r9 ] && docker ps --format '{{.Names}}' | grep -qx plex; then
  tok=$(sudo -n grep -o 'PlexOnlineToken="[^"]*"' "/srv/docker/plex/Library/Application Support/Plex Media Server/Preferences.xml" 2>/dev/null | cut -d'"' -f2)
  if [ -n "$tok" ]; then
    st=$(curl -s -m 8 -H 'Accept: application/json' "http://127.0.0.1:32400/media/grabbers/devices?X-Plex-Token=$tok" \
         | python3 -c "import json,sys; print(','.join(d.get('status','?') for d in json.load(sys.stdin)['MediaContainer'].get('Device',[])))" 2>/dev/null)
    [ "$st" = alive ] || bad "Plex Live TV tuner status is '${st:-unknown}' (expected alive)"
  fi
fi

# ---- report -----------------------------------------------------------------------------------------------------
{ echo "$TS host=$HOST mode=$MODE problems=${#problems[@]} fixes=${#fixes[@]}"
  for p in "${problems[@]}"; do echo "  PROBLEM: $p"; done
  for f in "${fixes[@]}"; do echo "  FIXED: $f"; done; } >> "$LOG"

cur=$(printf '%s\n' "${problems[@]}" | sort)
prev=$(cat "$LAST" 2>/dev/null)
printf '%s' "$cur" > "$LAST"
post=0
[ "$MODE" = --boot ] && post=1
[ "$MODE" = --test ] && post=1
[ "${#fixes[@]}" -gt 0 ] && post=1
[ "$cur" != "$prev" ] && post=1
if [ "$post" = 1 ] && [ -n "${FLEET_HEALTH_WEBHOOK:-}" ]; then
  if [ "${#problems[@]}" -eq 0 ]; then head="✅ **$HOST** healthy"; else head="❌ **$HOST**: ${#problems[@]} problem(s)"; fi
  [ "$MODE" = --boot ] && head="$head (after boot)"
  [ "$MODE" = --test ] && head="$head (manual test run)"
  [ "${#problems[@]}" -eq 0 ] && [ -n "$prev" ] && [ "$MODE" != --boot ] && head="✅ **$HOST** recovered: all checks passing"
  body="$head"
  for p in "${problems[@]}"; do body+=$'\n'"• $p"; done
  for f in "${fixes[@]}"; do body+=$'\n'"🔧 $f"; done
  python3 -c 'import json,sys; print(json.dumps({"username":"Fleet Health","content":sys.argv[1][:1900]}))' "$body" \
    | curl -s -m 10 -H 'Content-Type: application/json' -d @- "$FLEET_HEALTH_WEBHOOK" >/dev/null \
    || echo "$TS host=$HOST WARNING: Discord post failed" >> "$LOG"
fi
[ "${#problems[@]}" -eq 0 ]
