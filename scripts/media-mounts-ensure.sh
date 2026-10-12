#!/bin/bash
# Boot-time mount guard (r9 and production; 2026-10-12, after the Oct 11-12 outages left r9's /mnt/nas failed and
# Plex running against an empty /data/media). Runs as root from media-mounts-ensure.service (Type=simple).
#   1. All guarded mounts up and visible -> restart any container whose bind mount predates the mount (it would
#      see an empty directory), start anything a previous run held, done.
#   2. Something missing -> stop every container that binds a guarded path (so nothing scans/writes onto an empty
#      mount point), then retry the fstab mounts forever (every RETRY_S) and start the held containers once the
#      mounts are real. The held list persists in $HELD, so a reboot while waiting doesn't strand them (Docker
#      does not restart explicitly stopped containers).
# Never touches containers with restart policy "no" (Pterodactyl game servers, managed by Wings). Changes no config.
# Alerting is the health check's job: fleet-healthcheck reports "NOT mounted" to Discord while this keeps retrying.
set -u
case "$(hostname)" in
  mediahub-r9)
    MOUNT_PATHS=(/mnt/nas /mnt/prod-internal /mnt/media)   # all must be mounted (and the probe visible)
    MOUNT_UNITS=(mnt-nas.mount mnt-prod-internal.mount mnt-media.mount)
    DEP_PATHS=(/mnt/nas /mnt/prod-internal /mnt/media)     # containers binding these are held while any is missing
    DEP_UNITS=(mnt-nas.mount mnt-prod-internal.mount mnt-media.mount)   # parallel to DEP_PATHS
    EXCLUDE=() ;;
  mediahub-production|mediahub-i7)
    MOUNT_PATHS=(/mnt/internal /mnt/nas /mnt/media)
    MOUNT_UNITS=(mnt-internal.mount mnt-nas.mount mnt-media.mount)
    DEP_PATHS=(/mnt/nas /mnt/media)                        # Calibre binds local ext4 (/mnt/internal): not held for NAS outages
    DEP_UNITS=(mnt-nas.mount mnt-media.mount)
    EXCLUDE=(netdata glances) ;;                           # read-only monitors: keep running so outage alerts still fire
  *) echo "media-mounts-ensure: no config for $(hostname); nothing to do"; exit 0 ;;
esac
PROBE="${PROBE:-/mnt/media/movies}"   # exists only when mergerfs has unioned a real branch
RETRY_S="${RETRY_S:-30}"              # PROBE/RETRY_S/HELD/DRYRUN overridable for testing
DRYRUN="${DRYRUN:-0}"                 # 1 = report what would be stopped, change nothing
HELD="${HELD:-/var/lib/media-mounts-ensure/held}"
mkdir -p "$(dirname "$HELD")"; touch "$HELD"
log(){ echo "media-mounts-ensure: $*"; logger -t media-mounts-ensure "$*"; }

all_up(){
  local p
  for p in "${MOUNT_PATHS[@]}"; do mountpoint -q "$p" || return 1; done
  timeout 15 test -d "$PROBE"
}
# Names of containers (running or restarting, restart policy != no) that bind a guarded path.
dependents(){
  local c src m pol
  while read -r c; do
    [ -z "$c" ] && continue
    [[ " ${EXCLUDE[*]} " == *" $c "* ]] && continue
    pol=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$c" 2>/dev/null)
    [ "$pol" = no ] && continue
    while read -r src; do
      for m in "${DEP_PATHS[@]}"; do
        case "$src" in "$m"|"$m"/*) echo "$c"; continue 3 ;; esac
      done
    done < <(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$c" 2>/dev/null)
  done < <(docker ps --filter status=running --filter status=restarting --format '{{.Names}}')
}
# Epoch of the newest activation among the mount units backing the guarded paths container $1 binds. A container is
# stale only if it started before THE mount it binds (a NAS that appears underneath an already-mounted mergerfs is
# visible to containers started after mergerfs, so it must not count).
bind_mount_epoch(){
  local src i t best=0
  while read -r src; do
    for i in "${!DEP_PATHS[@]}"; do
      case "$src" in "${DEP_PATHS[$i]}"|"${DEP_PATHS[$i]}"/*)
        t=$(date -d "$(systemctl show -P ActiveEnterTimestamp "${DEP_UNITS[$i]}")" +%s 2>/dev/null || echo 0)
        [ "$t" -gt "$best" ] && best=$t ;;
      esac
    done
  done < <(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$1" 2>/dev/null)
  echo "$best"
}
start_held(){
  local c
  while read -r c; do
    [ -z "$c" ] && continue
    docker start "$c" >/dev/null 2>&1 && log "started $c (held until mounts were up)" || log "could not start $c"
  done < "$HELD"
  : > "$HELD"
}

if ! all_up; then
  # Hold: record first, then stop, so a crash between the two cannot lose track.
  mapfile -t deps < <(dependents)
  if [ "$DRYRUN" = 1 ]; then log "DRYRUN: mounts missing; would stop ${#deps[@]}: ${deps[*]}"; exit 0; fi
  for c in "${deps[@]}"; do grep -qx "$c" "$HELD" || echo "$c" >> "$HELD"; done
  [ "${#deps[@]}" -gt 0 ] && log "mounts missing; stopping ${#deps[@]} dependent container(s): ${deps[*]}"
  for c in "${deps[@]}"; do docker stop -t 30 "$c" >/dev/null 2>&1; done
  until all_up; do
    systemctl start "${MOUNT_UNITS[@]}" 2>/dev/null
    all_up || sleep "$RETRY_S"
  done
  log "mounts up after ${SECONDS}s"
fi

# Mounts are real. Start whatever was held (also covers a boot where a previous run was interrupted).
[ "$DRYRUN" = 1 ] || start_held
# Any container still running whose bind mount was taken before the mount existed sees an empty directory.
mapfile -t deps < <(dependents)
for c in "${deps[@]}"; do
  mt=$(bind_mount_epoch "$c")
  st=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$c")" +%s 2>/dev/null || echo 0)
  if [ "$st" -gt 0 ] && [ "$st" -lt "$mt" ]; then
    if [ "$DRYRUN" = 1 ]; then log "DRYRUN: would restart $c (started before the mounts existed)"; continue; fi
    log "$c started before the mounts existed; restarting it"
    docker restart "$c" >/dev/null 2>&1
  fi
done
log "done"
exit 0
