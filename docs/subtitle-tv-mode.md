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

### animetosho and anime coverage (2026-11-17, read-only)
- **animetosho (Bazarr provider) is not working:** its feed host `feed.animetosho.org` returns NXDOMAIN from the zone itself (SOA from animetosho.org's nameserver, same answer from 1.1.1.1 and our AdGuard; not our filtering). The main site resolves. Bazarr throttles it every ~10 minutes ("Name does not resolve"). Last download from it: 2026-09-26 (154 in total, mostly Spanish). Third-party catalog notes say AnimeTosho stopped ingesting new torrents on 2026-05-09 and the provider is archive-only (not verified on their own site). **Decision (Jay): leave enabled** (disabling is a global Bazarr setting); revisit if it stays dead.
- **Other anime providers:** the ones named for Bazarr+ (jimaku, kitsunekko) serve Japanese subtitles, not English, so they would not fill the English gap. No English anime source better than the ones already enabled was found for Bazarr; this is a research gap, not a finished answer.
- **Is it only HxH?** Latest English subtitle source per episode, from Bazarr history (3,442 anime episodes in 49 series vs 7,164 standard episodes in 155): whisperai 15% of anime episodes vs 6% of standard. It is concentrated in a few long series: Gintama 93% whisperai, Pokemon 70%, Black Clover 47%, Hunter x Hunter 26%, One Piece 16%. Most others are 0-5% (Attack on Titan, Demon Slayer, My Hero Academia, Dr. Stone, Monster, Fullmetal Alchemist: Brotherhood and others come from opensubtitles/subdl). So it is series-specific, not anime across the board. Whisperai output is the fallback when providers fail, so a high share marks series that providers cannot supply.

### Episode replace tool built (2026-11-17), plan mode only so far
`scripts/subtitle-replace-tv.py` (sibling of the movie tool, reuses its backup/restore/ledger/Bazarr helpers; not cron, not in the wrapper). `--plan` measures with tv-v1.1 and lists candidates; `--run` and `--restore` exist but have not been used on a live file.
- **Rule:** `judge_tv` = tv-v1.1 (limit 0.75 s, one stray window allowed) plus the overlap check (5%). Tests in `test_subtitle_replace_tv.py` use the real windows from the spot checks (S05E02, HxH S02E42, S02E16) and pass.
- **Guard:** per show. The Plex query lists episode sessions (show title and file); the tool stops only if the show being worked on is playing, or an episode session cannot be identified. Movies, live TV and other shows never block. A session whose Part has no `file` key (seen for a Sopranos stream) is handled, not a crash.
- **Candidates:** Bazarr episode endpoints (`/providers/episodes`, `/episodes/blacklist`, `/episodes/subtitles`); subsource is tried after opensubtitles and subdl; whisperai last; no direct-subsource path (no IMDb id per episode).
- **No Bazarr download on record** (older or hand-added file): measured from the file on disk, nothing blacklisted for it.
- **First plan run:** Suits S05E02 PASS (not a target, as seen by eye); Suits S04E02 FAIL (+2.0 s), Tier B, 29 candidates + 1 whisperai.

### First live run: Suits S04E02 (2026-11-17), stopped by hand, nothing changed
Tier B, current sub: windows read +1.6, +2.0, +1.6, +0.5, +0.5, -1.0 s (a spread of about 3 s, not a constant shift). Seven candidates were downloaded and measured (opensubtitles x2, subdl x5); all seven FAILED, each with its worst window between +2.0 and +2.3 s. Stopped after 7 of 29 candidates (SIGINT); the Tier B backup was put back byte for byte.
**Correction:** the first write-up said every source read the same constant +2 s and the video was simply 2 s off. That was an overreach: +2.0 is only the largest window, the ledger does not keep the per-window readings for candidates, and the current file is not a constant shift at all (the shift planner refuses it: spread over 0.5 s). What the data supports: the timing varies by a couple of seconds across the episode, and several different releases show the same worst-case reading. A cause (an episode cut that differs from the release the subtitles were made for) is plausible but not shown.
**Problem found and fixed:** a candidate Bazarr wrote under a different name (`.en.srt` while the original was `.en.hi.srt`) was left in the folder when the run was interrupted. The interrupt handler now removes any English subtitle written since the start of the run other than the original, after restoring the backup. The stray file was removed by hand.
**Shift step added:** the TV tool now corrects a candidate that reads a clean constant offset (shift planner: 3 or more usable windows within 0.5 s of each other), measures the corrected text, and accepts it only if it PASSes tv-v1.1. It is not a fix for varying offsets like S04E02. Candidate rows in the ledger should keep their per-window readings (to be done) so a pattern across candidates can be seen.

### How the 78 flagged episodes fail (2026-11-17, from the measurements; no files touched)
- 19: coverage or too few windows with enough cues (missing dialogue, truncated, or too sparse to measure).
- 17: fewer than 3 usable windows (scattered readings: wrong episode or a different cut).
- 42 had 4-6 usable windows with varying offsets: only **2 fit a straight line** within 0.4 s (Suits S04E14, S06E06) and **0 are a clean constant shift** (spread under 0.5 s). Others slope or wander by 1-3 s (S04E12, S04E13, S06E01, S06E02, S04E02).
So simple automatic corrections (constant shift, linear stretch) would fix very few; replacement with a different subtitle is the main route. The shift step stays in the tool (harmless: a corrected text must still PASS) but is not expected to matter. Candidate ledger rows now keep the six per-window offsets so patterns across candidates can be checked.

### Suits S06E15: first real TV replacement (2026-11-17), pending Jay's eyes
Tier B, current sub: 14% of cues overlapping (merged tracks). 11 candidates tried in 14 minutes: 8 FAIL on timing (two families of subtitle, one drifting to +12 s and one to +7 s against this video; four of them were the same SVA text served under different names), one FAIL on the same 14% overlap (a RARBG WEB-DL text, probably the same as the original), and #11 PASS: subdl "Suits Season 6 S06 720p BluRay 2CH x265 HEVC Complete", windows [+0.08, -0.04, +0.06, +0.10, +0.09, -0.11], 968 cues, 0 overlapping, ends at 40.9 minutes. Accepted at 22:24; the original is backed up in `~/subtitle-sweep/backups/2026-11-17/Suits_S06E15_*`.
Found: the accepted text styles every line (`<font color=#FFFF00><i><b>`), so it is 112 KB against 75 KB and will display yellow, bold and italic in clients that render markup. Timing is right; the style is not the rule's concern. Decision pending with Jay (keep, strip the tags, or restore).
Tool changes from this run: identical text is no longer re-measured (hash of the downloaded file), and the Bazarr blacklist call for a Tier B rejection returns HTTP 500 ("can only delete subtitles files", because the path is deliberately a non-existent `.rejected` name) after writing the row; harmless and the same trick as the movie tool.
**Jay's review of Suits S06E15 (Plex, 2026-11-17):** timing in sync throughout (skipped around the episode), one set of subtitles on screen. Yellow text noted; Jay does not mind it and the file size difference is acceptable. Decision: keep as is, no tag stripping. If uniform styling ever matters, options are stripping tags or accepting that external subs may be yellow. S06E15 is accepted: first reviewed TV replacement.

### Hunter x Hunter S01E02: second reviewed TV replacement (2026-11-17), first anime
Tier B, current sub failed (wrong episode: window at -9.8 s, scattered). The file has one audio track, English (a dub), so the measurer ran against English audio, not Japanese. 12 candidates + 1 whisperai; candidate 1 (opensubtitles, a release labelled "- 102") failed (-6, -1.6, -45, +1.0, -49, -1.8 s), candidate 2 (opensubtitles, HorribleSubs "- 02") PASSED at 22:43 after 2 tries: windows [-59.0, -0.1, +0.05, -0.1, -0.09, -0.1], accepted through the one-stray-window allowance (the -59 s window is the first, around the 2-minute mark, probably the opening song). 387 cues, 5 overlapping (1.3%), no markup, ends at 23.6 minutes. Jay checked in Plex: synced, no stacked subs, makes sense with what is said ("a win for anime"). Original backed up.
Takeaways: the stray-window allowance did its job here and is worth keeping; a mislabelled release name ("102" for episode 2) fails cleanly on the measurement; anime with an English dub can be measured, anime with Japanese-only audio is still "needs review".

### Suits S02E16: third TV replacement (2026-11-17), awaiting Jay's eyes
Tier B, current sub failed (windows -48 to +53 s, last cue at 112% of the runtime, i.e. longer than the video). 37 candidates + 1 whisperai were available; candidates 1-5 failed (two families reading [-4, -2.5, +2, -50, +33, +9.5] and one at +6), candidate 6 PASSED after 6 tries and 8 minutes: subdl "Suits.S02.1080p.BluRay.x265-YAWNiX", windows [+0.01, 0.0, +0.01, 0.0, 0.0, -0.02], 889 cues, 0 overlapping, no markup, ends at 42.7 minutes of a 43.1-minute video. Whisperai was not needed. The BluRay cut matched where the HDTV releases did not.
**Jay's review of Suits S02E16 (Plex, 2026-11-17):** subtitles line up. Accepted. Three TV replacements are now reviewed and accepted (Suits S06E15, Hunter x Hunter S01E02, Suits S02E16), each with the original backed up, no whisperai needed.

### Batches 1 and 2 (2026-11-17 to 18): ten more replacements, all clean; whisperai never needed
Run one episode at a time by `~/subtitle-sweep/tv_batch.py` (continues past a "show being watched" skip, stops on anything that is not a clean replacement). Early-whisperai rule added first: after 8 different failed texts the tool skips to whisperai (never triggered).
**Batch 1** (all replaced): Suits S04E12 (11 tries, opensubtitles DVDRip, worst window 0.12 s, 32 lines with markup), Suits S04E13 (1 try, HDTV-ASAP, worst 0.57 s), Suits S06E06 (2 tries, subdl KILLERS, worst 0.73 s, limit 0.75), Hunter x Hunter S01E01 and S01E10 (2 tries each, HorribleSubs, stray first window of -45 and -56 s, the opening song).
**Batch 2** (all replaced, newest first): Suits S09E09, S09E07, S08E09, S08E07 (subdl "Suits season N 1080p x265" packs) and S07E09 (BluRay YAWNiX); worst windows 0.03-0.12 s, no overlaps, 4-9 tries, 5-10 minutes each.
Pattern: the Suits library files are 1080p x265 encodes, and subtitle packs for the same encode (1080p x265 or BluRay) match where HDTV-era subtitles do not. The two weakest passes are S04E13 and S06E06 (worst windows 0.57 s and 0.73 s); Jay asked to check those two and one of the Hunter x Hunter episodes by eye. Not yet reviewed by Jay: all ten of the above.
