# Sonarr — same-title series matched the wrong show

## 2026-07-28 — Goosebumps 2023 reboot imported into Goosebumps (1995)

**Symptom:** Plex showed `Goosebumps (1995)` with 10 episodes. Playing "The
Haunted Mask (1)" actually played "Say Cheese and Die" — every episode in the
season was the 2023 reboot under a 1995 episode title.

**Cause:** the release `Goosebumps.S01.WEBRip.EAC3.5.1.1080p.x265-iVy` (NZBgeek,
season pack) carries **no year**. Sonarr parsed it as:

```
parsed series title : Goosebumps
parsed year         : 0
releaseType         : SeasonPack
MATCHED TO          : Goosebumps (1995)  tvdb=78790
```

Only the 1995 series existed in the library, so "Goosebumps" resolved to it with
no ambiguity to flag. The 1995 season 1 has 19 episodes, so incoming E01–E10 all
landed on real episode slots, and Sonarr renamed **by position**:

| Source (2023) | Imported as (1995) |
|---|---|
| E01 Say Cheese and Die | The Haunted Mask (1) |
| E04 Go Eat Worms | The Girl Who Cried Monster |
| E06 Night of the Living Dummy Part 1 | Welcome to Camp Nightmare (2) |
| E10 Welcome to Horrorland | Night of the Living Dummy II |

Sonarr never inspects file contents, and does not cross-check a file's mtime
(2023-11-27) against the episode's air date (1995-10-27). E03 is
"The Cuckoo Clock of Doom" in *both* series, because the reboot reuses classic
book titles — a spot check on that episode would have looked correct.

This is not a misconfiguration. Given a year-less release and a single matching
series title, Sonarr behaved as designed.

## Fix applied 2026-08-03

Added `Goosebumps (2023)` (tvdb 415840) as its own series and moved the 10 files
onto it. All files verified byte-identical before and after.

**Gotcha:** the `DownloadedEpisodesScan` command **ignores the `seriesId`
parameter** and re-matches by title parsing — it put the files straight back on
the 1995 series. Forcing the assignment requires the `ManualImport` command with
explicit `seriesId` and `episodeIds` per file:

```
GET  /api/v3/manualimport?folder=<path>&filterExistingFiles=false
POST /api/v3/command
     {"name":"ManualImport","importMode":"move",
      "files":[{"path":...,"seriesId":131,"episodeIds":[13102],
                "quality":...,"languages":...}]}
```

Note also that adding a series via the API does **not** create its folder on
disk; `manualimport` returns `DirectoryNotFoundException` until it is created.

## Open risk

`Goosebumps (1995)` is still in Sonarr, monitored, now with 0 of 83 episodes.
With both series present, a year-less "Goosebumps S01" release is genuinely
ambiguous, so an automatic search for the 1995 series could grab the reboot pack
again. Either unmonitor the 1995 series until it is wanted, or grab it via
interactive search rather than trusting RSS.
