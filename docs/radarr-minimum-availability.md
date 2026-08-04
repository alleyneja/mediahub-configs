# Radarr minimum availability — why monitored movies never auto-grabbed

## 2026-08-04 — `Released` → `In Cinemas`

**Symptom:** Toy Story 5 was requested via Seerr ~a year ago and stayed monitored
but never downloaded, even though good 1080p releases existed. An interactive
search in Radarr found and grabbed it instantly.

**Root cause:** two independent gates both had to pass, and one was shut.

1. **Minimum availability.** Radarr will not search or RSS-grab a movie until it
   is "available." With `minimumAvailability = released`, that means TMDB must
   publish a digital or physical release date. Toy Story 5 had:

   ```
   status=inCinemas  inCinemas=2026-06-17
   digitalRelease=null  physicalRelease=null  → isAvailable=false
   ```

   TMDB never filled in the digital date, so Radarr considered the movie
   non-existent indefinitely. Interactive search bypasses this gate entirely,
   which is why manual search worked.

2. **Nothing ever re-searches.** Neither Radarr nor Sonarr has a scheduled
   missing-search task (verified against `/api/v3/system/task`). A grab can only
   happen at two moments:
   - **RSS Sync** (Radarr 30 min, Sonarr 15 min) — matches only what is on the
     indexer feed *right now*; it never looks backwards.
   - **A one-off search** — Seerr approval, "search on add", or a manual click.

   Seerr fired exactly one search a year ago, found nothing, and never tried
   again. RSS sync was the only remaining path and gate 1 had closed it.

**Applied:**

| Where | Setting | Before | After |
|---|---|---|---|
| Radarr — 934 existing movies | `minimumAvailability` | `released` | `inCinemas` |
| Seerr — default Radarr server | `minimumAvailability` | `released` | `inCinemas` |

Both were required. Seerr stamps `minimumAvailability` onto every movie it adds,
so changing only Radarr would have fixed the backlog while every new request
arrived with the old behavior.

40 movies remain at `tba` (all VeggieTales, all already downloaded, none
missing) — left alone as irrelevant.

**Why this does not invite cam rips:** the quality profile is doing the
gatekeeping instead, and does it better. `Streaming Minimums` allows only
`HDTV-1080p, WEBDL-1080p, WEBRip-1080p` — CAM, TELESYNC, SCREENER, DVD, Bluray
and 720p are all rejected. Opening the availability gate lets RSS *watch* a film
from theatrical release; the profile still decides what is good enough to grab.

**Residual risk (accepted):** Radarr assigns quality by parsing the release name,
so a cam uploaded as `Movie.2026.1080p.WEBRip.x264` parses as WEBRip-1080p and
can slip through. The fix would be a release profile with `CAM, HDCAM, TS,
TELESYNC, HDTS, TC, TELECINE, SCR, SCREENER, HDTC` in the ignored list — not
applied, Jay opted to notice and delete the rare bad grab instead. Radarr
currently has no release profiles.

**Immediate effect:** The Odyssey (in cinemas since 2026-07-15, digital date
null — the identical situation) flipped to available and is now eligible for
RSS auto-grab. 19 other blocked movies remain correctly blocked; they have no
cinema release yet.

**Rollback:** pre-change state was a uniform `released` on those 934 movies;
re-run the bulk edit with `minimumAvailability: released` via
`PUT /api/v3/movie/editor` and revert the Seerr value in
`/srv/docker/seer/settings.json`.

## Related

- `arr-upgrade-policy.md` — `upgradeAllowed=false` is unrelated to this; it
  blocks *replacing* an existing file and never blocks a first grab.

## Still open

Sonarr has **628 missing monitored episodes** — the same "nothing ever
re-searches" gap, but on the TV side. A scheduled missing-search would fix it;
deliberately not done, since 628 episodes fanned out across nine indexers at
once risks rate-limiting. Needs a batched approach.
