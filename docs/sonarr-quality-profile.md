# Sonarr — "Streaming Minimums" quality profile

Sonarr stores quality profiles in `sonarr.db`, not in this repo. This file records
deliberate changes so they can be reproduced after a restore.

## 2026-08-02 — stop perpetual upgrade re-downloads

**Symptom:** Sonarr repeatedly re-downloaded episodes already in the library.
Since 2026-07-01, 690 of 1,708 grabs (40%) were for episodes it had already imported.

**Cause:** two settings on profile 1 kept nearly every episode permanently
"upgrade eligible":

- Quality cutoff was `Bluray-1080p`, the top of the allowed list, leaving 2,687 of
  5,693 files (47%) below cutoff and perpetually searched.
- `minUpgradeFormatScore` was `1`, so a one-point custom-format gain justified a
  full re-download. Most gains came from `x265` (+25) or `Dual Audio` (+50) —
  the latter a title regex that fires on Hindi-English and Japanese-English
  releases, not Spanish.

**Changes applied** (via API, `PUT /api/v3/qualityprofile/1`):

| Setting | Before | After |
|---|---|---|
| `cutoff` | `7` (Bluray-1080p) | `1002` (WEB 1080p group) |
| `minUpgradeFormatScore` | `1` | `100` |
| `cutoffFormatScore` | `200` | `200` (unchanged, deliberate) |

`cutoffFormatScore` stays at 200 on purpose. 200 is only reachable by a release
matching both `Language: English` (+100) and `Language: Spanish` (+100), so
episodes remain in the search pool — but with `minUpgradeFormatScore=100`, only a
genuine Spanish gain triggers a re-download. Junk +25/+50 upgrades are ignored.

Note: `cutoff` must reference the group id, not the bare quality, because
WEBDL-1080p is grouped with WEBRip-1080p as "WEB 1080p" (id 1002). Those two are
therefore treated as equivalent for cutoff purposes.

**Effect:** cutoff-unmet dropped from 2,704 to 1,139 immediately.

Only 288 of 5,693 files (5.1%) actually contain Spanish audio, so the Spanish
custom format is retained as a preference rather than moved to a future
`sonarr-es` instance.

## Known unfixed issue — Sonarr/SABnzbd queue blindness

Separate from the above, Sonarr 4.0.17 intermittently fails to read the SABnzbd
queue:

```
System.ArgumentOutOfRangeException: TimeSpan overflowed because the duration is too long.
  at ...Sabnzbd.JsonConverters.SabnzbdQueueTimeConverter.ReadJson(...)
```

SAB reports a `timeleft` large enough to overflow `TimeSpan`, which throws away the
*entire* queue fetch. Sonarr then believes nothing is downloading and re-grabs
releases already in flight: 645 episode/release pairs were grabbed more than once
(903 redundant grabs), 27 of 46 recent duplicates within 60 minutes of each other.

First seen 2026-05-07; 58 occurrences on 2026-08-01, 48 on 2026-08-02.

Trigger is low throughput — newshosting connections are capped at 4 (see SABnzbd
burst-download mitigation), so a large queue produces very long ETAs. The profile
fix above should reduce queue size and therefore ETA length, but the underlying
parsing bug remains in 4.0.17. Candidate fix: bump Sonarr to 4.0.19.
