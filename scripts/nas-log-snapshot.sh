#!/bin/bash
# Best-effort NAS-side evidence pull for the permissions monitor. Never fails the caller:
# if the NAS is unreachable (SSH normally closed - see plan Global Constraints), it logs
# that and exits 0. Readable UGOS logs are grepped near the given timestamp; the root-only
# ones (no sudo available to alleyneja) are reported as metadata only, with a pointer to
# check them via the UGOS Log Manager app in the browser.
set -uo pipefail

TS="${1:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
DATE_ONLY="${TS:0:10}"
NAS_HOST="192.168.0.23"
NAS_USER="alleyneja"

echo "--- NAS log snapshot for $TS ---"

if ! timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=5 "$NAS_USER@$NAS_HOST" true 2>/dev/null; then
    echo "NAS SSH unreachable from this host right now (port closed, or no key from here) - skipping."
    exit 0
fi

READABLE_LOGS=(
    "/var/ugreen/log/media_serv.log"
    "/var/ugreen/log/media_serv_worker.log"
    "/var/ugreen/log/thumb_core.log"
    "/var/ugreen/log/thumb_worker_0.log"
    "/var/ugreen/log/thumb_worker_background_0.log"
)
ROOT_ONLY_LOGS=(
    "/var/ugreen/log/filemgr_serv_fileOperations.slog"
    "/var/ugreen/log/filemgr_serv.slog"
    "/var/ugreen/log/index_serv_inotify.slog"
    "/var/ugreen/log/index_serv_event.slog"
    "/var/ugreen/log/ctl_serv.slog"
    "/var/ugreen/log/ctl_serv.slog.1"
    "/var/ugreen/log/storage_serv.slog"
    "/var/ugreen/log/storage_serv_ugvolume.slog"
    "/var/ugreen/log/taskmgr_serv.slog"
    "/var/ugreen/log/jobmgr_serv.slog"
)

echo "-- readable logs, lines matching $DATE_ONLY --"
for log in "${READABLE_LOGS[@]}"; do
    echo "= $log ="
    timeout 8 ssh -o BatchMode=yes "$NAS_USER@$NAS_HOST" \
        "grep -F '[$DATE_ONLY' '$log' 2>/dev/null | tail -30" 2>/dev/null
done

echo "-- root-only logs (no sudo available): metadata only, check content via UGOS Log Manager app --"
for log in "${ROOT_ONLY_LOGS[@]}"; do
    timeout 8 ssh -o BatchMode=yes "$NAS_USER@$NAS_HOST" \
        "stat -c '%n  size=%s  mtime=%y' '$log' 2>/dev/null" 2>/dev/null
done

echo "--- end NAS log snapshot ---"
