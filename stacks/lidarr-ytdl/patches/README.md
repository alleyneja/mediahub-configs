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
