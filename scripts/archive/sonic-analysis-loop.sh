#!/bin/bash
# Loops: restart Plex → trigger MusicAnalysis → wait for completion → repeat until done

TOKEN=$(grep -o 'PlexOnlineToken="[^"]*"' '/srv/docker/plex/Library/Application Support/Plex Media Server/Preferences.xml' | cut -d'"' -f2)
BLOBS_DB="/srv/docker/plex/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.blobs.db"
LIB_DB="/srv/docker/plex/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
LOG_PATH="/srv/docker/plex/Library/Application Support/Plex Media Server/Logs/Plex Media Server.log"

blob_count() {
  sqlite3 "$BLOBS_DB" "SELECT COUNT(*) FROM blobs WHERE blob_type=7;"
}

albums_remaining() {
  sqlite3 "$LIB_DB" "ATTACH '$BLOBS_DB' AS bdb;
    SELECT COUNT(*) FROM (
      SELECT album.id
      FROM metadata_items album
      JOIN metadata_items track ON track.parent_id = album.id AND track.metadata_type = 10
      LEFT JOIN bdb.blobs b ON b.linked_id = track.id AND b.blob_type = 7
      WHERE album.metadata_type = 9
      GROUP BY album.id
      HAVING COUNT(b.id) = 0
    );"
}

wait_for_plex() {
  echo "  Waiting for Plex to come up..."
  for i in $(seq 1 60); do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:32400/identity" 2>/dev/null)
    if [ "$STATUS" = "200" ] || [ "$STATUS" = "401" ]; then
      echo "  Plex is up."
      return 0
    fi
    sleep 2
  done
  echo "  ERROR: Plex did not come up in time."
  exit 1
}

wait_for_completion() {
  local LOG_MARK
  LOG_MARK=$(wc -l < "$LOG_PATH")
  echo "  Watching logs for completion signal..."
  local waited=0
  while true; do
    sleep 15
    waited=$((waited + 15))
    # Check if the "0 batches" completion line appeared after our log mark
    if tail -n +"$LOG_MARK" "$LOG_PATH" 2>/dev/null | grep -q "Performed sonic analysis on 0 batches"; then
      echo "  Analysis run completed."
      return 0
    fi
    # Also check for "section 3 was active already" meaning it stalled before even starting
    if tail -n +"$LOG_MARK" "$LOG_PATH" 2>/dev/null | grep -q "section 3 was active already"; then
      echo "  Ghost detected early — bailing out for restart."
      return 0
    fi
    if [ $waited -ge 3600 ]; then
      echo "  Timed out waiting for completion after 60 min."
      return 1
    fi
    echo "  Still running... (${waited}s, blobs so far: $(blob_count))"
  done
}

TOTAL_ALBUMS=$(sqlite3 "$LIB_DB" "SELECT COUNT(*) FROM metadata_items WHERE metadata_type=9;")
echo "=== Sonic Analysis Loop ==="
echo "Total albums: $TOTAL_ALBUMS"
echo "Starting blobs: $(blob_count)"
echo ""

CYCLE=0
while true; do
  REMAINING=$(albums_remaining)
  if [ "$REMAINING" -eq 0 ]; then
    echo "All albums analyzed! Final blob count: $(blob_count)"
    break
  fi

  CYCLE=$((CYCLE + 1))
  echo "[Cycle $CYCLE] Albums remaining: $REMAINING | Blobs: $(blob_count)"

  echo "  Restarting Plex..."
  docker restart plex > /dev/null 2>&1
  sleep 5
  wait_for_plex
  sleep 5

  echo "  Triggering MusicAnalysis..."
  curl -s -X POST "http://localhost:32400/butler/MusicAnalysis" -H "X-Plex-Token: $TOKEN" > /dev/null

  wait_for_completion || break

  echo "  Cycle done. Blobs now: $(blob_count)"
  echo ""
done

echo "=== Loop finished ==="
echo "Final blob count: $(blob_count)"
echo "Albums remaining: $(albums_remaining)"
