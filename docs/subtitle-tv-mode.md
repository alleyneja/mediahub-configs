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

## Benchmark step 2: TV measurer results (2026-11-17, read-only)
`scripts/subtitle-gate-benchmark/tv/` (`measure_tv.py` runs in the bazarr container, `score_tv.py` scores, `tv_label.py` labels). 8 known-good episodes
(6 Suits, 2 Hunter x Hunter, all agreeing with their embedded track) x 18 variants (untouched + the 17 corruptions used for movies) = 144 measurements per
window configuration. About 2 minutes per episode for 18 variants.

| | A: 6 windows of 180 s | B: 5 windows of 300 s |
|---|---|---|
| False positives on untouched good subs | 0 of 8 | 0 of 8 |
| Shifts of 5 s or more | 48 of 48 | 48 of 48 |
| Shifts of 1 s | 16 of 16 | 16 of 16 |
| Framerate scales | 21 FAIL, 3 UNVERIFIABLE, 0 passed | 17 FAIL, 7 UNVERIFIABLE, 0 passed |
| Truncation | 16 of 16 | 16 of 16 |
| 10-minute gap | 8 of 8 | 8 of 8 |
| Step (different cut) | 8 of 8 | 7 of 8, 1 UNVERIFIABLE |
| Drift | 16 of 16 | 16 of 16 |

Chosen for the TV rule (working name tv-v1): configuration A, parameters cutoff 10 s, 10+ cues per window, 6 of 6 windows with enough cues,
5 usable windows to PASS, max offset 0.5 s, max spread 1.0 s, last cue at 90% or more. Same consensus rule (3+ windows agreeing on a large offset = FAIL).
Limits, stated plainly: 8 episodes is a small sample (the 2% false-positive bar cannot be shown at this size, as with movies); the 10-minute gap is
easier to catch in a 24-minute episode than in a film, so 8 of 8 does not transfer; only 2 anime episodes; no TV-specific real known-bad set yet.

The 8 episodes that differed from their embedded track, measured with A: 2 PASS (Hunter x Hunter S02E06 and S02E15: five windows within 0.1 s, one
window thrown by something near the start, so the embedded track is just a different translation), 6 FAIL. Not all 6 are bad:
- Suits S04E05 FAILs by 0.02 s (one window reads -0.52 s; the rest within 0.45 s). A false positive from the 0.5 s limit, the same effect as The Empire Strikes Back (+2.2 s, looked fine to Jay).
- Hunter x Hunter S02E33: four windows near 0, one reads +1.6 s and one -35 s. Unclear.
- Suits S01E01 (an 81-minute pilot), Hunter x Hunter S01E01, S01E10, S02E24: windows read -60 to +60 s in no pattern, i.e. a different cut or a subtitle that does not belong to this video, or the measurer lost alignment. These four need Jay's eyes before any label.
Open question for the rule: the 0.5 s limit. Raising it to 1.0 s would stop false flags like S04E05 but lets subs 1 s off pass. Decide after the review.

### Pilot episodes reviewed by eye (Jay, 2026-11-17)
The four episodes whose windows read -60 to +60 s in no pattern were all genuinely wrong, so the rule had no false alarm among them:
- Hunter x Hunter S01E01 and S01E10: subtitles from a different episode of the same show (dialogue about Nen does not match). Wrong-episode subs read as scattered offsets.
- Suits S01E01: wrong, offset by minutes. Against the embedded track there is no single constant offset: the SRT ends at 72.7 min, the embedded track at 80.7 min, so it is a different cut, not a shift. The measurer searches +-60 s, so anything larger reads as scatter.
- Hunter x Hunter S02E24: slow drift of about 0.4 s across the episode against the embedded track (+0.37 s early, -0.04 s late), visible by eye. The scanner flagged it, but its window readings (-53 to +20 s) are measurement noise, not the true drift. Rough: the embedded track is segmented differently (360 cues vs 242).
Takeaways: scattered offsets are a strong sign of a bad sub on these shows; candidates for such files cannot be fixed by a constant shift, only replaced; the 0.5 s limit question (S04E05, S02E24, Empire) remains open.

### Rule adjustment tv-v1.1 and the full pass over both pilot shows (2026-11-17)
**Library pass (173 episodes with `.en.srt`, read-only, about 40 minutes at 2 workers):** Suits 59 PASS / 14 FAIL of 73; Hunter x Hunter 36 PASS / 43 FAIL / 21 UNVERIFIABLE of 100. A further 109 episodes have only `.en.hi.srt` (not missing; the job list had looked for `.en.srt` only) and are measured separately.
**Spot check by eye (Jay, 7 episodes):** flagged and really bad: Suits S06E15 (2-3 overlapping tracks in one file, 14% of cues overlap), Hunter x Hunter S02E26 (off), plus the earlier S01E01 (Suits), HxH S01E01, S01E10, S02E24 (drift). Flagged and fine: Suits S05E02, Suits S04E05, HxH S02E42; each had one or two windows just over the limit. Scattered or large offsets were right every time. Suits S06E11 (4% overlap) looked fine by eye.
**Change (tv-v1.1):** max offset 0.5 s becomes 0.75 s, and one outlier window is allowed if the other five pass. Benchmark unchanged: untouched good subs 8/8 PASS, 1 s shifts 16/16 FAIL, drift/truncation/gap/step all FAIL; scales 19 FAIL + 5 UNVERIFIABLE (from 21 + 3), none pass. 1.0 s was rejected: 4 of 16 one-second shifts passed. Effect on the library pass: PASS 95 to 98, FAIL 57 to 54.
**Overlap check (proposed, not yet in a tool):** FAIL if 5% or more of cues overlap the previous cue by over 0.3 s. Only S06E15 (14%) is flagged; the next is S06E11 (4%, fine by eye).
**Open:** HxH has 17 coverage/too-few-windows FAILs and 21 UNVERIFIABLE that are not yet explained (anime audio is the weak case); Suits S02E16 and HxH S01E02 (scattered) not yet checked by eye. Code: `scripts/subtitle-gate-benchmark/tv/rescore_tv.py`.
**Update (Jay, by eye):** Hunter x Hunter S01E02 definitely offset; Suits S02E16 offset in some places and not others, mostly offset (scanner: scattered -48 to +53 s). Scattered or large offsets are now 7 of 7 real across the pilot spot checks; false flags so far were only near-limit cases, fixed by tv-v1.1.
