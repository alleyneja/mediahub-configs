#!/bin/bash
# Checks Plex EPG DB on startup and resets stale state if programme data is missing.
# Run via cron @reboot with a delay to give Plex time to start.

PLEX_CONFIG="/srv/docker/plex/Library/Application Support/Plex Media Server"
MAIN_DB="$PLEX_CONFIG/Plug-in Support/Databases/com.plexapp.plugins.library.db"
EPG_DIR="$PLEX_CONFIG/Plug-in Support/Databases"
TOKEN=$(grep -oP 'PlexOnlineToken="\K[^"]+' "$PLEX_CONFIG/Preferences.xml" 2>/dev/null)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Wait for Plex to be available (up to 3 minutes)
log "Waiting for Plex..."
for i in $(seq 1 36); do
    if curl -s --max-time 3 "http://localhost:32400/identity" -o /dev/null 2>/dev/null; then
        log "Plex is up."
        break
    fi
    sleep 5
done

# Find the EPG DB
# NOTE: the path contains spaces, so the glob must be expanded by bash itself
# (an unquoted `ls $VAR` word-splits it into bad paths and silently finds nothing).
shopt -s nullglob
EPG_MATCHES=("$EPG_DIR"/tv.plex.providers.epg.xmltv-*.db)
shopt -u nullglob
EPG_DB="${EPG_MATCHES[0]:-}"
if [ -z "$EPG_DB" ]; then
    log "No EPG DB found — Plex hasn't created one yet, skipping."
    exit 0
fi

# Check programme count
# Open read-only: Plex holds this DB live. A failed query must NOT be reported as 0,
# or a transient lock would look like an empty guide and delete a healthy database.
PROG_COUNT=$(python3 -c "
import sqlite3
try:
    conn = sqlite3.connect('file:$EPG_DB?mode=ro', uri=True)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM metadata_items')
    print(cur.fetchone()[0])
    conn.close()
except Exception as e:
    print('ERROR:', e)
")

log "EPG programme count: $PROG_COUNT"

# Bail out unless we got a real number — never destroy state on an unreadable DB.
if ! [[ "$PROG_COUNT" =~ ^[0-9]+$ ]]; then
    log "Could not read programme count — leaving EPG untouched."
    exit 1
fi

if [ "${PROG_COUNT:-0}" -lt 100 ]; then
    log "EPG is empty or near-empty — resetting stale lastChunkEndedAt and triggering rebuild..."

    python3 -c "
import sqlite3, json
DB = '$MAIN_DB'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute(\"SELECT id, extra_data FROM media_provider_resources WHERE identifier='tv.plex.providers.epg.xmltv'\")
row = cur.fetchone()
if row:
    extra = json.loads(row[1])
    extra['pv:lastChunkEndedAt'] = '0'
    extra.pop('pv:timeOfLastRefresh', None)
    cur.execute('UPDATE media_provider_resources SET extra_data=? WHERE id=?', (json.dumps(extra), row[0]))
    conn.commit()
    print('Reset lastChunkEndedAt to 0 for resource id', row[0])
else:
    print('No XMLTV EPG resource found in DB')
conn.close()
" 2>&1

    # Delete stale EPG DB so Plex rebuilds from scratch
    rm -f "$EPG_DB" "${EPG_DB}-shm" "${EPG_DB}-wal"
    log "Deleted stale EPG DB files."

    # Wait a moment for Plex to notice the DB is gone
    sleep 10

    # Trigger guide refresh
    if [ -n "$TOKEN" ]; then
        RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
            "http://localhost:32400/butler/RefreshEpgGuides" \
            -H "X-Plex-Token: $TOKEN")
        log "RefreshEpgGuides response: HTTP $RESP"
    else
        log "No Plex token found — skipping RefreshEpgGuides trigger."
    fi
else
    log "EPG looks healthy, nothing to do."
fi
