# Subsource Direct Candidates: Decision Record

**Status:** DRAFT 2026-11-16, waiting for Jay's go. NOTHING BUILT OR RUN beyond the two read-only searches in "Evidence".
**Issue:** alleyneja/mediahub-issues#73. **Parent:** [subtitle-replace-t0.md](subtitle-replace-t0.md)

## Problem
Bazarr's manual search (`GET /providers/movies`) never lists subsource's English subs. Bazarr's subsource provider
(`subliminal_patch/providers/subsource.py`, `query()`) searches ONE language, `list(languages)[0]`, taken from an
unordered set. A manual search passes the profile's English and Spanish, and subsource got Spanish every time. Bazarr's
automatic missing-search passes English only, so subsource works there: since Nov 1 it supplied 113 English downloads,
more than opensubtitles.com (52) or subdl (28) for movies. So the replace tool has been using two of the three movie
sources. Whisperai made 4 of the 5 T0 replacements partly because of this.

## Evidence (2026-11-16)
- Manual search showed zero subsource English candidates for all 5 T0 movies and for 4 movies that got subsource English
  subs in the last week; Spanish subsource results did appear (1-2).
- Two read-only API queries from inside the bazarr container for Hoot (imdb tt0453494): English returned 9 results,
  Spanish 1. Nothing downloaded, nothing changed. The code reading and the query agree; not tested end to end.

## Decision proposed
Add subsource as an extra candidate source in the replace loop, behind a switch (`--direct-subsource`, off by default).
Bazarr is not changed in any way. Turning the switch off returns to the current tool exactly.

## Design
- **API** (as Bazarr's provider uses it): `GET https://api.subsource.net/api/v1/movies/search?searchType=imdb&imdb=ID`
  to get the movie id; `GET .../subtitles?language=english&limit=100&movieId=ID`; `GET .../subtitles/<id>/download`
  returns a zip. The api key is read from Bazarr's config at run time, held in a variable, never printed, logged or
  written. The key travels in the query string, so the tool never logs URLs.
- **Download to a temp file first.** Unlike Bazarr's POST download (which writes over the library path), this is true
  download-then-swap: the zip goes to a scratch dir, only the `.srt` is read from it (member names ignored, size cap
  2 MB, no extraction to disk by path), copied into the bazarr container's `/tmp`, measured by the gate's measurer.
  The library file is not touched until a candidate passes.
- **Raw, unsynced.** Bazarr normally runs ffsubsync on a download. These candidates are measured as downloaded, so a
  constant offset fails rule v1. That rejects some good-but-shifted subs; a later step could apply the measured shift,
  not in this version.
- **Accept** only on rule v1 PASS: atomic rename into the library path, previous file backed up first (same as now), the
  old file removed only after the better one is in place. Bazarr's history will not show it; our ledger does, and a
  Bazarr disk scan picks the file up.
- **Order:** direct subsource candidates are mixed into the same list as Bazarr's candidates, deduplicated by release
  name and uploader, under the same 20-minute budget before whisperai.
- **Rate limits:** at most 2 search calls and 1 download per candidate, 2 s between calls, stop the title on HTTP 429.
  A title with 10 candidates costs about 12 API calls.
- **Phase 0 (no swaps):** for the 4 whisperai titles (Maid in Manhattan, Hoot, Delivery Man, Fired Up!) fetch and
  measure all subsource English candidates and report how many PASS raw. That answers "what does it give us" before any
  file changes. Swaps for those titles happen only if a candidate passes AND Jay agrees to replace the whisperai sub.

## Risks and what is not known
- Subsource's terms of use for API access are not checked; the key is already used by Bazarr for the same endpoints.
- Whether Jay's plan is paid, and its quota, is unknown to me; the tool stops on 429 and logs the count of calls.
- Untrusted zip content: handled as above (read-only, size cap, no path use).
- Not reported upstream: a bug report for Bazarr (single language from a set) is possible; draft only on Jay's request.

## Rollback
Do not pass `--direct-subsource`. Replaced files restore from `~/subtitle-sweep/backups/` with `--restore`.
