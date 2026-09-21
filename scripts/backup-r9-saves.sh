#!/bin/bash
# Nightly pull of mediahub-r9's emulator saves/states onto production.
# r9 is the master for saves (fleet-architecture.md, Phase 2). Additive only:
# nothing is ever deleted from the backup, and a file that gets overwritten is
# first kept under _versions/<date>/. Run from production's cron, not from r9.
set -uo pipefail

SRC_HOST=192.168.0.22
DEST=/mnt/media/arcade/backups/r9-saves
LOG=/home/jay/logs/backup-r9-saves.log
STAMP=$(date +%Y%m%d)
mkdir -p "$DEST" "$(dirname "$LOG")"

# name|path on r9 (relative to /home/jay unless absolute)
SOURCES=(
  "arcade-saves|/mnt/internal/arcade/saves"
  "arcade-states|/mnt/internal/arcade/states"
  "rpcs3-savedata|.config/rpcs3/dev_hdd0/home"
  "rpcs3-flatpak-savedata|.var/app/net.rpcs3.RPCS3/config/rpcs3/dev_hdd0/home"
  "ryujinx-user|.var/app/io.github.ryubing.Ryujinx/config/Ryujinx/bis/user"
)

fail=0
{
  echo "=== $(date -Is) start"
  for entry in "${SOURCES[@]}"; do
    name=${entry%%|*}; path=${entry#*|}
    [[ $path == /* ]] || path="/home/jay/$path"
    if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$SRC_HOST" "test -d '$path'"; then
      echo "MISSING source on r9: $name ($path)"; fail=1; continue
    fi
    mkdir -p "$DEST/$name"
    if rsync -a --backup --backup-dir="$DEST/_versions/$STAMP/$name" \
         -e "ssh -o BatchMode=yes" "$SRC_HOST:$path/" "$DEST/$name/"; then
      echo "ok: $name"
    else
      echo "FAILED rsync: $name (exit $?)"; fail=1
    fi
  done
  echo "=== $(date -Is) done fail=$fail"
} >> "$LOG" 2>&1

exit $fail
