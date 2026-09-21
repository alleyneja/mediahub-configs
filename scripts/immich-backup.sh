#!/bin/bash
# Nightly Immich backup. Replaces ~/immich-db-backup.sh (moved into the repo 2026-09-21; decision D11 in
# docs/fleet-architecture.md). Runs from production's user crontab in the same 02:30 slot as before.
#
#  1. Database dump  -> /srv/docker/immich/backups (14 days). Written to a temp file and kept ONLY if valid, so a
#     failed dump can never sit there looking like a backup.
#  2. Originals mirror -> production's drive. The NAS holds the canonical originals (upload/); production keeps a
#     second, independent copy in photos/immich-originals-mirror (outside the Immich library path, so Immich never
#     sees duplicates). ADDITIVE ONLY: --ignore-existing and no --delete, so photos deleted in Immich stay in the
#     mirror until someone prunes them by hand.
#  3. Heartbeat -> Uptime Kuma push monitor "Immich nightly backup" (25 h). Sent as "up" only if every step
#     succeeded; on any failure an immediate "down" is sent. If the job never runs, the missing heartbeat alerts.
#
# Secret: KUMA_PUSH_URL lives in scripts/immich-backup.env (gitignored, mode 600).
set -uo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
source "$HERE/immich-backup.env"

LOG=/home/jay/logs/immich-backup.log
BACKUP_DIR=/srv/docker/immich/backups
KEEP_DAYS=14
SRC="${IMMICH_MIRROR_SRC:-/mnt/nas/photos/immich/upload}/"          # override only for testing
DST=/mnt/internal/photos/immich-originals-mirror/
MIN_FILES=20000                                                       # sanity floor (library has about 24,000)

failed=""
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
fail() { failed="$failed $1"; log "FAILED: $2"; }
mkdir -p "$(dirname "$LOG")" "$BACKUP_DIR"
log "start"

# 1. database dump
TMP=$(mktemp -p "$BACKUP_DIR" .dump.XXXXXX)
if docker exec immich-postgres pg_dump --clean --if-exists --dbname=immich --username=immich | gzip > "$TMP" \
   && gzip -t "$TMP" && [ "$(stat -c %s "$TMP")" -gt 1000000 ]; then
  mv "$TMP" "$BACKUP_DIR/immich-db-$(date +%Y%m%d-%H%M%S).sql.gz"
  find "$BACKUP_DIR" -name "immich-db-*.sql.gz" -mtime "+$KEEP_DAYS" -delete
  log "database dump ok"
else
  rm -f "$TMP"; fail db "database dump missing, corrupt or suspiciously small"
fi

# 2. originals mirror (NAS canonical -> production drive)
if ! mountpoint -q /mnt/nas; then
  fail mirror "/mnt/nas is not mounted"
else
  n_src=$(sudo -n find "$SRC" -type f 2>/dev/null | wc -l)
  if [ "$n_src" -lt "$MIN_FILES" ]; then
    fail mirror "source has only $n_src files (floor $MIN_FILES): refusing to call this a success"
  else
    sudo -n mkdir -p "$DST"
    if sudo -n rsync -a --numeric-ids --ignore-existing "$SRC" "$DST" >> "$LOG" 2>&1; then
      n_dst=$(sudo -n find "$DST" -type f | wc -l)
      if [ "$n_dst" -ge "$n_src" ]; then log "originals mirror ok ($n_src on NAS, $n_dst in mirror)"
      else fail mirror "mirror has $n_dst files but the NAS has $n_src"; fi
    else
      fail mirror "rsync exited non-zero"
    fi
  fi
fi

# 3. heartbeat
if [ -z "$failed" ]; then
  curl -fsS -m 20 "$KUMA_PUSH_URL" >> "$LOG" 2>&1 && log "heartbeat sent" || log "WARNING: heartbeat could not be sent"
  log "done: ok"; exit 0
else
  # Build the "down" URL from the part before "?" (a bash ${var/x/y} replacement would treat "&" as "the matched text").
  DOWN_URL="${KUMA_PUSH_URL%%\?*}?status=down&msg=failed:${failed// /_}&ping="
  curl -fsS -m 20 "$DOWN_URL" >> "$LOG" 2>&1 || true
  log "done: FAILED steps:$failed"; exit 1
fi
