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
- ~~Which two shows for the pilot?~~ Decided 2026-11-17: Suits (134 episodes, live action) and Hunter x Hunter (2011) (148, anime).
- ~~Plex guard~~ Decided: block only the show being watched. (Movies now work the same way per title, see subtitle-enforce-mode.md.)
- ~~Anime without an English audio stream~~ Decided: "needs review" for now, to be revisited with a better plan (Jay: whisperai on Japanese is too many moving parts; it matters most for shows he cannot follow by ear).

## Benchmark step 1: known-good labels (2026-11-17, read-only)
Survey of the two pilot shows: every episode has an English `.srt`, and in a sample of 12 per show every file has an embedded English
subtitle track, which gives a timing reference independent of the audio (the movie benchmark needed a second signal the same way).
`scripts/subtitle-gate-benchmark/tv/tv_label.py` compares each sampled `.srt` with its embedded track (nearest cue start): 16 episodes sampled
(8 per show, spread across the run), **8 agree closely** (median under 0.25 s, 90% or more of cues within 0.5 s) and 8 differ by 0.2-1.1 s
median (Hunter x Hunter S01E01 and S01E10 about 1.1 s; Suits S01E01 0.7 s). The 8 that differ are not known bad: the embedded track may be a
different translation or segmentation. They need the audio measurement before they are labelled either way. Labels: `~/subtitle-sweep/tv-bench/labels.json`
(library titles, not committed).
Next: a TV measurer (shorter windows, two window lengths compared), run on the agreeing episodes plus the 17 corruptions used for movies, then score.
