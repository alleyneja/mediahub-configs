# lidarr-downloader length-check patch (#80)

`processing.py` replaces the image's `/app/processing.py` (bind mount, read-only).
`processing.py.orig` is the unmodified file from the pinned image digest
(sha256 4028099cf537ac9f6280ce8c872f5df118bee1ecab9fcdb7f0a6484f71af8e75);
`processing.py.diff` is the change, suitable for an upstream PR.

**What it does:** after each YouTube download, measure the real file. If it is
shorter than the MusicBrainz length, or shorter than the length the source was
listed at, by more than `duration_tolerance` (10s), the candidate is rejected and
its URL banned, and the existing candidate loop moves on to the next one (album
playlist track -> fresh YouTube search). Longer files are left alone (alternate
editions; AcoustID and the audit cover wrong songs).

**Why:** search results and the official-playlist fast path only carry a *listed*
length. Jodeci "Get On Up" was listed at 249s but the stream served was 200s, and
nothing measured it until the audit did.

**On an image bump:** `diff processing.py.orig <new /app/processing.py>`; if
unchanged, keep the patch, otherwise re-apply `processing.py.diff` by hand.
**Rollback:** delete the `./patches/processing.py` volume line and `docker compose up -d`.

**YouTube bot-wall circuit breaker:** if 5 downloads in a row fail with "Sign in to
confirm you're not a bot" (a success resets the count; other errors neither count
nor reset), the downloader sets `queue_paused`, stops the active job (which also
clears the queue) and posts "YouTube bot check: downloads paused" to #media-alerts
(`album_error`). Resume by hand with `POST /api/download/queue/pause` once the block
lifts (Oct 17's took ~6h). Motivation: the scheduler would otherwise keep retrying
through a block, probably prolonging it.

**Live-stream guard (Oct 19):** before each download, `_is_live_stream()` does a
metadata-only yt-dlp lookup (`process=False`, ~1-2s) and rejects the candidate
(outcome "live stream", URL banned) if `live_status` is `is_live`/`is_upcoming`.
Why: "24/7 lofi radio" streams match generic track titles ("Sad Lofi"); the length
check only runs *after* a download finishes, and a live stream never does, so
ffmpeg recorded one for 40+ min and wedged the whole album job. Note
`is_live` is None under `process=False`; only `live_status` is reliable. The check
fails open (any lookup error -> normal download).
