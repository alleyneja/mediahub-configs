# Subtitle Gate: TV Episodes Decision Record

**Status:** DRAFT 2026-11-17. Requirements agreed with Jay; design and rule not yet decided. Nothing is built and nothing in TV is touched.
**Issue:** alleyneja/mediahub-issues#73. **Parents:** [subtitle-enforce-mode.md](subtitle-enforce-mode.md), [subtitle-quality-gate.md](subtitle-quality-gate.md)

## Requirements (Jay, 2026-11-17)
**Must do:** measure English episode subtitles against the video's audio and, when one is clearly wrong, replace it with a candidate that passes, the way movies are handled.
**Scope:** every episode eventually (Jay: "most of them are probably right", so the aim is to find the minority that are not). Library size from Bazarr's database, 2026-11-17:
10,598 episodes in 211 shows, about 8,300 with an English download on record, roughly 8 times the movie library.
**Order:** same as movies: new downloads first (every 15 minutes), then the existing library in tranches (Plex watch list first, newest first), pilot before bulk.
**Risk rules:** same as movies. A sub is removed before a better one passes only for a useless one (Tier A: last cue under 50% of runtime or under 20 cues). Otherwise snapshot, candidate, measure, restore unless it passes (Tier B). Never touches Spanish files or global Bazarr settings. UNVERIFIABLE is never replaced. Coverage-only FAILs go to "needs review". Discord post per replacement, spot-check about one in five, PAUSE file, `--restore`.
**Must not affect:** Plex playback (see the guard below), the movie path.

## What is different from movies (why this is not a copy of the movie tool)
1. **Rule v1 does not fit.** It uses 7 windows of 480 s. A 22-minute episode is 1,320 s, so the windows would overlap and cover most of the file, and "10+ cues in all 7 windows" is meaningless. TV needs its own rule (working name tv-v1): fewer, shorter windows, its own cue thresholds, and its own benchmark (synthetic shifts caught, no false positives on known-good subs) before any replacement is automatic. This is the largest piece of work.
2. **Plex guard.** The movie tool stops only for movie streams and ignores episodes. For TV the file being watched (or the next episodes of the show being binge-watched) must not change underneath a viewer. Proposed: stop for any active stream of the same show; other streams do not block. To be decided.
3. **Same-release sets.** Episodes of one season usually come from the same release, so a bad candidate tends to fail on every episode of it. Candidate and blacklist handling are per episode in Bazarr, so a bad provider result is retried per episode unless we remember by season. To be decided.
4. **Audio.** Anime and dual-audio releases: the measurer picks the first English non-commentary stream; for Japanese-only anime with English subtitles there is no English stream, so the audio guard sends it to "needs review". A rule for these (measure against the Japanese track, which English subs also follow) needs a decision and a benchmark.
5. **Cost.** At about a minute per measurement, 8,300 episodes is roughly 140 hours of measuring. Nightly 100 would take about 3 months, so the order matters: watched shows first.
6. **Bazarr API.** Episodes use their own endpoints (subtitle download, blacklist, history by episode id), not the movie ones the replace tool calls.

## Proposed plan (not approved)
1. Benchmark tv-v1 read-only against episodes (same method as the movie benchmark). No live changes. Needs the 50-title movie sweep finished so the two do not compete for CPU.
2. Decision on the open questions above, recorded here.
3. Build `subtitle-replace.py` episode support (or a sibling tool) and an episode path in the enforce wrapper, behind report-only first.
4. Pilot on 2 shows Jay picks, review by eye, then a tranche of 25 episodes, then the same go-ahead sequence as movies.

## Open questions for Jay
- Which two shows for the pilot?
- Plex guard: block only the show being watched, or all episode streams?
- Anime without an English audio stream: measure against the original-language track, or leave as "needs review"?
