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

# ACTUAL_LANG is Sonarr/Radarr's own reconciled "languages" field -- the
# determination the app itself trusts and displays, not the raw container
# tag. The raw tag (mediaInfo.audioLanguages) is kept separately, for
# context only: it is frequently blank/"und" (undetermined) because a lot
# of WEB-DL/WEBRip releases never bother tagging the audio stream's
# language at all. Treating a missing raw tag as "confirmed wrong" was a
# real bug -- Animal Farm (2026) and Batman (1989) both alerted on this,
# and both were false alarms; Radarr's own reconciled field said English
# for both the whole time. Also: jq's `//` only falls through on `null`,
# not on `""`, so chaining the raw tag into the fallback (as the original
# version did) silently produced a blank result whenever the raw tag was
# an empty string rather than absent -- that's the Batman "Actual audio:"
# blank case specifically.
ACTUAL_LANG=$(echo "$FILE_JSON" | jq -r '(.languages // [] | map(.name) | join(","))')
RAW_TAG=$(echo "$FILE_JSON" | jq -r '(.mediaInfo.audioLanguages // "")')

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

log "actual=$ACTUAL_LANG raw_tag=$RAW_TAG grabbed_as=$GRAB_LANG source=$SOURCE_TITLE"

# Only alert when Radarr/Sonarr's own reconciled language for the file is
# both non-empty (we actually know something) and disagrees with what was
# assumed at grab time. An empty ACTUAL_LANG means the app itself couldn't
# determine a language either -- that's genuine uncertainty, not a proven
# mismatch, so stay quiet rather than cry wolf on a guess.
if [[ -n "$ACTUAL_LANG" ]] && [[ "$GRAB_LANG" == *"English"* ]] && [[ "$ACTUAL_LANG" != *"English"* ]]; then
  if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
    log "MISMATCH found but DISCORD_WEBHOOK_URL not set in .env, alert not sent"
    exit 0
  fi
  RAW_TAG_NOTE="${RAW_TAG:-not tagged by the release}"
  if [ "$RAW_TAG" = "und" ]; then
    RAW_TAG_NOTE="und (release never tagged an audio language at all)"
  fi
  PAYLOAD=$(jq -n \
    --arg content "Audio language mismatch: **${TITLE}**
File: \`${REL_PATH}\`
Grabbed as: ${GRAB_LANG}  (release: ${SOURCE_TITLE})
Sonarr/Radarr's own detected audio: ${ACTUAL_LANG}
Raw container tag (context only): ${RAW_TAG_NOTE}" \
    '{content: $content}')
  curl -s -H "Content-Type: application/json" -d "$PAYLOAD" "$DISCORD_WEBHOOK_URL" >>"$LOG_FILE" 2>&1
  log "ALERT sent"
else
  log "no mismatch (or inconclusive), no alert"
fi
