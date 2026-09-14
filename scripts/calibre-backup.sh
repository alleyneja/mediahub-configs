#!/bin/bash
# Calibre metadata.db backup, via SQLite's Online Backup API (safe against the
# live WAL-mode database without needing to stop calibre-web-automated) --
# same method already used for Vaultwarden's db.sqlite3.
#
# metadata.db already corrupted once (2026-08-02) and was recovered by luck
# with nothing changed afterward; this had no backup of any kind until now.
set -euo pipefail

LIBRARY_DIR=/mnt/internal/ebooks/calibre-library
BACKUP_DIR=/srv/docker/calibre-web/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"

sqlite3 "$LIBRARY_DIR/metadata.db" ".backup '$BACKUP_DIR/metadata-$STAMP.db'"
gzip "$BACKUP_DIR/metadata-$STAMP.db"

find "$BACKUP_DIR" -name "metadata-*.db.gz" -mtime "+$KEEP_DAYS" -delete
