#!/bin/bash
# Boot-time mount guard for mediahub-r9 (2026-10-12; after the Oct 11-12 outage left /mnt/nas failed and Plex
# running against an empty /data/media). Runs as root from media-mounts-ensure.service.
#   1. Mounts all up and visible  -> make sure Plex sees them (restart it if its bind mount predates the mount).
#   2. Something missing          -> stop Plex immediately (so it never scans an empty library), retry the fstab
#                                    mounts every 30s for up to 20 min, then start Plex again.
#   3. Still missing at deadline  -> leave Plex STOPPED, exit 1. fleet-healthcheck runs after this and reports it.
# It only ever starts the mount units and the plex container; it changes no config.
set -u
MOUNT_UNITS=(mnt-nas.mount mnt-prod-internal.mount mnt-media.mount)
MOUNT_PATHS=(/mnt/nas /mnt/prod-internal /mnt/media)
PROBE=/mnt/media/movies            # exists only when mergerfs has unioned a real branch
GUARD=plex
DEADLINE_S=1200
log(){ echo "media-mounts-ensure: $*"; logger -t media-mounts-ensure "$*"; }

all_up(){
  local p
  for p in "${MOUNT_PATHS[@]}"; do mountpoint -q "$p" || return 1; done
  timeout 15 test -d "$PROBE"
}
plex_sees_media(){ timeout 20 docker exec "$GUARD" test -d /data/media/movies 2>/dev/null; }
plex_running(){ [ -n "$(docker ps -q --filter "name=^${GUARD}$")" ]; }

if all_up; then
  if plex_running && ! plex_sees_media; then
    log "mounts up but $GUARD sees an empty /data/media; restarting it"
    docker restart "$GUARD" >/dev/null
  else
    log "mounts up, nothing to do"
  fi
  exit 0
fi

held=0
if plex_running; then
  log "mounts missing; stopping $GUARD so it cannot scan an empty library"
  docker stop "$GUARD" >/dev/null && held=1
fi

while ! all_up; do
  if [ "$SECONDS" -ge "$DEADLINE_S" ]; then
    log "GAVE UP after ${DEADLINE_S}s; mounts still missing. $GUARD left stopped."
    exit 1
  fi
  systemctl start "${MOUNT_UNITS[@]}" 2>/dev/null
  all_up || sleep 30
done

log "mounts up after ${SECONDS}s"
if [ "$held" = 1 ]; then docker start "$GUARD" >/dev/null && log "$GUARD started"; fi
exit 0
