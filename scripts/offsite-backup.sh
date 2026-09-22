#!/bin/bash
# Nightly OFF-SITE backup, encrypted, to Jay's Google Drive (docs/backup-and-storage.md, D1).
# Scope is deliberately small: household essentials that would be a real problem to lose, not the
# media library or the 80 GB photo library (both already have on-site copies elsewhere; see D3/D11
# in fleet-architecture.md and the requirements conversation this followed).
#
# Mechanism: restic (versioned, encrypted, deduplicated snapshots) writing through rclone's Google
# Drive backend. Runs at 03:00, after the other nightly dump jobs (02:30-02:50) have produced fresh
# database dumps, so this reads finished dumps rather than a live database file.
#
# The repository password (scripts/offsite-backup.password, gitignored, mode 600) is the single
# point of failure for restoring this backup: it also lives in Jay's Apple Keychain and on paper
# (NOT only in this file, and NOT in Vaultwarden -- the vault is one of the things this backs up).
# It is passed via --password-file, never as an environment variable set through `env VAR=value` on
# a sudo command line -- that form is visible to any local user via `ps` for as long as the process
# runs. (A first version of this script did exactly that; the exposed password was rotated out of
# the repository before this one was used for real. See docs/backup-and-storage.md D1.)
set -uo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
# shellcheck disable=SC1091
source "$HERE/offsite-backup.env"
# sudo does not inherit exported vars by default; RESTIC_REPOSITORY and RCLONE_CONFIG are not
# secret (a location and a path), so they are passed the same way. The password is not: it goes in
# via --password-file, which restic reads directly and which never appears in `ps` or argv.
# rclone (spawned by restic as a subprocess) looks for its config under whichever user is running
# it; as root (via sudo) that is /root/.config/rclone, which does not exist. Point it at the one
# real copy instead of duplicating the Google credential into root's own config.
run_restic() { sudo -n env RESTIC_REPOSITORY="$RESTIC_REPOSITORY" RCLONE_CONFIG=/home/jay/.config/rclone/rclone.conf restic --password-file "$HERE/offsite-backup.password" "$@"; }

LOG=/home/jay/logs/offsite-backup.log
KEEP_DAILY=30
KEEP_MONTHLY=12

# Existing dumps (already produced by the 02:30-02:50 jobs) plus live file trees for things that
# are not databases. Database data directories are deliberately NOT included raw -- their own
# scripts already take a consistent snapshot (pg_dump / sqlite3 .backup) into these directories.
SOURCES=(
  /srv/docker/vaultwarden/backups
  /srv/docker/nextcloud/backups
  /mnt/media/nextcloud
  /srv/docker/authentik/backups
  /srv/docker/immich/backups
  /mnt/media/arcade/backups/r9-saves
  /srv/docker/caddy/data/caddy/pki/authorities/local
  /home/jay/.ssh
)

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
mkdir -p "$(dirname "$LOG")"
log "start"

present=()
for s in "${SOURCES[@]}"; do
  if [ -e "$s" ]; then present+=("$s"); else log "WARNING: source missing, skipping: $s"; fi
done

heartbeat() {  # $1 = up|down, $2 = message
  if [ -z "${KUMA_PUSH_URL:-}" ]; then log "no Kuma monitor configured yet, skipping heartbeat"; return; fi
  if [ "$1" = up ]; then curl -fsS -m 20 "$KUMA_PUSH_URL" >> "$LOG" 2>&1 && log "heartbeat sent (up)" || log "WARNING: heartbeat could not be sent"
  else curl -fsS -m 20 "${KUMA_PUSH_URL%%\?*}?status=down&msg=$2&ping=" >> "$LOG" 2>&1 || true; fi
}

if run_restic backup "${present[@]}" --tag nightly >> "$LOG" 2>&1; then
  log "backup ok"
  run_restic forget --keep-daily "$KEEP_DAILY" --keep-monthly "$KEEP_MONTHLY" --prune >> "$LOG" 2>&1
  heartbeat up
  log "done: ok"
  exit 0
else
  log "FAILED: restic backup exited non-zero"
  heartbeat down restic_backup_failed
  log "done: FAILED"
  exit 1
fi
