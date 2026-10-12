# Bazarr subtitle sync: findings and changes (2026-10-12)

## Findings
- Bazarr syncs only at download time (`subtitles/processing.py`). Replacing a movie
  file triggers a re-index, never a re-sync. Same filename => old `.srt` stays attached.
- Global sync (offset + framerate scale) cannot fix subtitles timed to a different cut.
  Audit of 40 random English-audio movies against the audio: ~48% clean, ~18% clearly
  off (linear drift or per-window mismatch), the rest ambiguous.
- Language profile "Streaming Minimums" has cutoff = Spanish, so English external subs exist
  only where Spanish was unavailable or fetched as a translation source.
  `use_embedded_subs: false` is on: embedded tracks do not count as existing.

## Change
- `subsync.no_fix_framerate`: true -> false (framerate correction now allowed on sync).
  Config backup: `~/config.yaml.bazarr.bak-*`.

## Measuring a subtitle against audio (ground truth)
Run ffsubsync (inside the bazarr container, `PYTHONPATH=/app/bazarr/bin/libs`) on 8-15 minute
audio windows and compare per-window offsets. A good sub reads ~0 in every window.
Windows reading near +/-60 s are search-limit artifacts (no peak), not measurements.

## Resident Evil (2026)
All human English candidates (subdl, opensubtitlescom, TS/CAM/WEB-DL labelled) were 2-40 s off
in some window. The whisperai provider output reads +0.2-0.3 s in every window and is in use.
The bad candidates are blacklisted (via the blacklist API with a non-existent path, because the
API deletes the file at `subtitles_path`) so the upgrade cycle cannot swap them back in.
