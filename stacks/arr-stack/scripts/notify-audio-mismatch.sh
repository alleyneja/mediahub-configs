#!/usr/bin/env bash
# Sonarr/Radarr "On Import" + "On Upgrade" custom script.
#
# Sonarr/Radarr score a release's language from the RELEASE TITLE at grab
# time (e.g. "[English-Sub]" gets parsed as English audio). The real audio
# language is only known after the file lands, via embedded MediaInfo tags.
# The two are never reconciled by the app itself (see Sonarr issue #7523),
# so a title that lied about its audio language sticks around forever,
# especially on profiles with upgradeAllowed=false.
#
# This script compares "what we assumed at grab time" against "what the
# file actually has" post-import, and posts a Discord alert on mismatch.
# It never deletes, blocklists, or re-searches anything — for currently
# airing/simulcast content (anime especially) Japanese-only audio is often
# the only thing that exists yet, so this is a review queue, not an
# enforcer.

set -uo pipefail

# discord.env is bind-mounted directly into /config by docker-compose.yml
# (both sonarr and radarr mount it read-only at this same path) -- it is
# NOT the same file as arr-stack/.env, which only feeds compose
# interpolation and is never visible inside these containers.
ENV_FILE="/config/discord.env"
LOG_FILE="/config/logs/audio-mismatch.log"

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

log() { printf '%s %s\n' "$(date -Iseconds)" "$1" >>"$LOG_FILE"; }

log "=== invoked ==="
env | sort | grep -E '^(sonarr|radarr)_' >>"$LOG_FILE"

if [ -n "${sonarr_eventtype:-}" ]; then
  APP="sonarr"
  EVENTTYPE="$sonarr_eventtype"
elif [ -n "${radarr_eventtype:-}" ]; then
  APP="radarr"
  EVENTTYPE="$radarr_eventtype"
else
  log "no recognized sonarr_*/radarr_* env vars, exiting"
  exit 0
fi

# Both apps use eventtype "Download" for On Import and On Upgrade.
if [ "$EVENTTYPE" != "Download" ]; then
  log "eventtype=$EVENTTYPE is not an import/upgrade, exiting"
  exit 0
fi

CONFIG_XML="/config/config.xml"
API_KEY=$(grep -oE '<ApiKey>[^<]+' "$CONFIG_XML" | cut -c9-)
PORT=$(grep -oE '<Port>[^<]+' "$CONFIG_XML" | cut -c7-)
BASE="http://localhost:${PORT}/api/v3"

if [ "$APP" = "sonarr" ]; then
  FILE_ID="${sonarr_episodefile_id:-}"
  TITLE="${sonarr_series_title:-unknown series}"
  REL_PATH="${sonarr_episodefile_relativepath:-unknown file}"
  FILE_JSON=$(curl -s "$BASE/episodefile/$FILE_ID?apikey=$API_KEY")
  # episodefile has no episodeId field -- Sonarr only hands it to us via this
  # env var. A series-wide history lookup (by seriesId) is NOT safe here: it
  # returns every episode's grabs, and picking "most recent" would attribute
  # a completely different episode's release to this file. Multi-episode
  # files get several ids; the first is good enough to identify the grab.
  EPISODE_ID="${sonarr_episodefile_episodeids%%,*}"
  HIST_JSON=$(curl -s "$BASE/history?episodeId=${EPISODE_ID}&apikey=$API_KEY")
else
  FILE_ID="${radarr_moviefile_id:-}"
  MOVIE_ID="${radarr_movie_id:-}"
  TITLE="${radarr_movie_title:-unknown movie}"
  REL_PATH="${radarr_moviefile_relativepath:-unknown file}"
  FILE_JSON=$(curl -s "$BASE/moviefile/$FILE_ID?apikey=$API_KEY")
  HIST_JSON=$(curl -s "$BASE/history/movie?movieId=$MOVIE_ID&eventType=1&apikey=$API_KEY")
fi

log "file_json: $FILE_JSON"

ACTUAL_LANG=$(echo "$FILE_JSON" | jq -r '
  (.mediaInfo.audioLanguages // (.languages // [] | map(.name) | join(",")))
')

# Most recent "grabbed" history record for THIS episode/movie specifically
# -- reflects the title-parsed language Sonarr/Radarr assumed when it
# decided this release was good enough.
#
# /api/v3/history?episodeId= returns a paged {records:[...]} object; the
# older /history/movie route returns a bare array. Branch on type
# explicitly -- `.[]? // .records[]?` is NOT a safe fallback here: on an
# object, `.[]?` already yields output (the scalar field values), so `//`
# never reaches `.records[]?`, and `select(.eventType==...)` then blows up
# trying to index a non-object.
HIST_FILTER='(if type == "array" then . else .records end) | [.[] | select(.eventType=="grabbed")] | sort_by(.date) | reverse | .[0]'
GRAB_LANG=$(echo "$HIST_JSON" | jq -r "${HIST_FILTER} | (.languages // [] | map(.name) | join(\",\"))")
SOURCE_TITLE=$(echo "$HIST_JSON" | jq -r "${HIST_FILTER} | (.sourceTitle // \"unknown release\")")

log "actual=$ACTUAL_LANG grabbed_as=$GRAB_LANG source=$SOURCE_TITLE"

# Only alert when the grab was scored as English but the real audio isn't.
if [[ "$GRAB_LANG" == *"English"* ]] && [[ "$ACTUAL_LANG" != *"English"* ]] && [[ "$ACTUAL_LANG" != *"eng"* ]]; then
  if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
    log "MISMATCH found but DISCORD_WEBHOOK_URL not set in .env, alert not sent"
    exit 0
  fi
  PAYLOAD=$(jq -n \
    --arg content "Audio language mismatch: **${TITLE}**
File: \`${REL_PATH}\`
Grabbed as: ${GRAB_LANG}  (release: ${SOURCE_TITLE})
Actual audio: ${ACTUAL_LANG}" \
    '{content: $content}')
  curl -s -H "Content-Type: application/json" -d "$PAYLOAD" "$DISCORD_WEBHOOK_URL" >>"$LOG_FILE" 2>&1
  log "ALERT sent"
else
  log "no mismatch, no alert"
fi
