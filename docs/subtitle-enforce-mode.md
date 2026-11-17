# Subtitle Gate: Enforce Mode (auto-replace) Decision Record

**Status:** APPROVED by Jay 2026-11-16 (record and building the wrapper). BUILT, NOT LIVE: `scripts/subtitle-enforce.py` is tested but has no cron line. Go-live (the cron line) needs Jay's separate OK.
**Issue:** alleyneja/mediahub-issues#73. **Parents:** [subtitle-quality-gate.md](subtitle-quality-gate.md), [subtitle-replace-t0.md](subtitle-replace-t0.md), [subtitle-subsource-direct.md](subtitle-subsource-direct.md)

## Requirements (what this must do, affect, and not affect)
**Must do:** when the gate flags a new English movie subtitle (rule v1 FAIL), replace it with a candidate that passes rule v1, without Jay doing it by hand.
**May touch:** English subtitle files of movies, Bazarr blacklist rows (via the existing tool), the replace-tool ledger and backups, one Discord post per replacement in #media-alerts.
**Must never:** remove a sub before a better one passes (exception, Jay's: Tier A, a useless sub under 50% of runtime or under 20 cues); change global Bazarr settings; touch the Spanish files; leave a title worse than before; touch a title during a movie stream or when the stream count is unknown.
**Must not affect:** Plex playback, TV episodes (report-only stays), UNVERIFIABLE subs, FAILs whose only reason is last-cue coverage.

## Entry condition (Jay, 2026-11-16)
Report-only exit met: 15 gated downloads (lowered from 30), every flag reviewed by Jay, precision judged against the 2% bar in the parent record. The make-up of the sample is recorded when the exit is declared (ordinary downloads vs replace-tool output), so the evidence is read honestly.

## Design
1. **Trigger (Jay, 2026-11-16):** Jay watches a movie the day he downloads it, so a nightly run is too slow. The gate and the wrapper run every 15 minutes, all day, under `flock`. A download is measured once it has settled (10 minutes, because Bazarr's sync rewrites the file), and a new FAIL movie goes straight to `subtitle-replace.py --run --direct-subsource`. Expected time from download to fixed sub: about 30-45 minutes. No Bazarr hook is used: changing a global Bazarr setting is a must-never, and polling gets nearly the same result. The replace tool's order is unchanged: subsource as-is, subsource shifted (constant offsets only), Bazarr candidates, whisperai after 20 minutes.
2. **Cap:** at most 5 replacements per run. The rest wait for the next run.
3. **Never auto-replaced, only reported as "needs review":** UNVERIFIABLE, and FAILs whose only reason is last-cue coverage.
4. **Guard:** a VOD movie stream, or an unknown stream count, stops the run and restores the original (existing behavior). Episodes and live TV do not block.
5. **Audio-stream guard (new):** if the video's first audio stream is not English or is labelled commentary, the title is "needs review", not measured and replaced. The measurer uses stream `0:a:0` today.
6. **Discord:** one post per replacement to #media-alerts: title, tier, how it was fixed (subsource, shifted, whisperai), backup path, restore command. A post for every title that ends unresolved or "needs review", so Jay knows before pressing play.
7. **Spot-check:** about one in five replacements is marked for Jay to eyeball. A bad one drops the system back to report-only until Jay says otherwise. Spot-check results are recorded in the ledger.
8. **Kill switch:** an empty file `~/subtitle-sweep/PAUSE` makes the wrapper do nothing. `--restore TITLE` undoes any single replacement.

## Library sweep mode (added 2026-11-16, Jay: "yes please")
`subtitle-enforce.py --sweep N` measures the next N library movies not yet handled (Plex watch list first, newest first; ambiguous titles are
never guessed, they fall back to library order) and sends FAILs through the same path a tick uses (audio guard, coverage-only to needs-review,
UNVERIFIABLE left alone, Plex movie guard, backup, Discord post, spot-check counter). It holds the replace lock, so scheduled ticks skip
meanwhile. It posts a tally when done, and resumes where it stopped if the guard interrupts it (measured titles are in the ledger).
Run by hand, one tranche at a time, with Jay's go: pilot already done (T0 5 + tranche 1 five + Injustice), next 25, then 50, then nightly 100.

## Not in scope
TV episodes, the Spanish files, the library sweep (still manual, in tranches, with Jay's confirmation in #73), the tail probe for credit-heavy films (planned in the parent record, section 10).

## Risks and what is not known
- The precision of rule v1 is measured on a small sample. The spot-check and the fall-back to report-only are the control.
- Whisperai output can mangle names and slang. It still has to pass rule v1, but rule v1 checks timing, not wording.
- Bazarr's own scheduled search can refill an empty English slot mid-run (seen once). Mitigated for Tier B by never emptying the slot.
- Candidate supply: some titles have no passing community sub. They end as whisperai or "unresolved".

## Rollback
Remove the wrapper line from cron (or create the PAUSE file). The gate itself keeps running report-only. Replaced files restore from `~/subtitle-sweep/backups/`.

## Built 2026-11-16 (not live)
`scripts/subtitle-enforce.py` (tick, `--dry-run`, `--status`, `--spot`, `--resume`), `test_subtitle_enforce.py` (4 tests), the replace tool's
`process()` and `movie_by_history_id()`, and the gate's `--no-discord`. A dry run against the last 72 hours of gate rows dropped every
stale row (the file had changed since it was measured), reported Home as coverage-only "needs review", and would have replaced nothing.
The staleness check compares the file's size and mtime, not just its path (a dry run caught that bug before go-live).

**Finding:** in a sample of 60 library movies, 7 (12%) have a non-English first audio stream, and all 7 have an English stream later.
The measurer reads stream `0:a:0` (the gate benchmark was built that way), so the audio guard would send about one movie in eight to
"needs review", for example Delivery Man (first stream Italian) and Fired Up! (Portuguese). DONE (Jay, 2026-11-16): the measurer now picks the first English non-commentary stream (falls back to the first), and the guard sends a
title to needs-review only when no such stream exists. Re-test against the English track: Delivery Man PASS (offsets within 0.1 s), Fired Up! PASS
(within 0.2 s), Idiocracy (single track, control) PASS and unchanged.

## Open items before go-live
- A live Discord test post, and one real end-to-end tick on a title Jay chooses.
- Kuma heartbeat for the wrapper (Jay's call; needs a Kuma stop, see `gotcha_kuma_monitor_db_insert`).
- Jay's approval of this record.

## Report-only exit: declared 2026-11-16
`subtitle-gate.py --summary`: **18 gated downloads** (PASS 12, FAIL 6, UNVERIFIABLE 0); all 6 flags reviewed by Jay, **5 confirmed bad** (83% live precision on flags).
Threshold was 15 (lowered from 30 by Jay). Exit condition: MET.

**Make-up of the sample, stated plainly:** 10 were ordinary new downloads (6 flagged, 4 PASS). The other 8 were outputs of the replace tool
(already passed rule v1 there, then re-measured by the gate: 8 of 8 PASS). So the independent evidence on false positives is the 10, not the 18.
The one false positive on a good sub is Home (2015): long credits, last cue at 90%. 1 false positive in 13 good subs is not under the 2% bar,
and 13 is too few to show it either way. What covers this in enforce mode: coverage-only FAILs go to "needs review" and are never replaced
(Home would not be touched), plus the spot-check, the one-in-five Discord flag, the PAUSE file, backups and `--restore`.
Jay reviewed all 10 replaced titles by eye after replacement: 10 of 10 good (New Moon re-checked on another client).
Measured with `--ignore-plex` once, with Jay's say-so, because a paused Sopranos episode on his Fire TV kept the strict guard closed for hours.

**Guard change (Jay, 2026-11-16):** the gate's guard now ignores paused sessions and stops only for active playback (playing, buffering, or no state reported). The replace tool's movie guard is unchanged (paused movies still count, because the file is still open). Both queries share one template in `subtitle-gate.py`. Tested with synthetic session data (unit test) and live. A test post to #media-alerts was sent with `subtitle-enforce.py --test-post`.

## Follow-up parked: rule v1.1, retry failed windows (2026-11-16)
Question (Jay): why was The Nun UNVERIFIABLE, and would choosing windows by where the audio has speech avoid it?
Study (read-only, `webrtcvad` + the same ffsubsync call): The Nun's 7 fixed windows had 20-94 cues and 32-58% speech-like audio, so they were not
empty of dialogue. 3 of 7 read 30-59 s anyway (ffsubsync lost the alignment, likely music/effects in a horror soundtrack). A scan of 18 overlapping
8-minute windows read within 0.35 s everywhere except the last window (running into the credits, 59 s). Windows moved by 1-2 minutes from a failed
one read about 0. Speech-picked windows fixed 1 of 3 failures, left one at 50 s, and produced a +3.2 s reading near the credits that the scan showed
to be noise. Conclusion: the sub is almost certainly fine; the weak point is one reading per window, not where the windows sit.
Proposed (not built): when a window reads beyond the 10 s cutoff, retry it about 2 minutes later before counting it as unusable. Requires the benchmark
to be re-run (96/96 shifts caught, 0 false positives) before adoption. UNVERIFIABLE stays "never replaced" until then. Expected payoff: small
(about 1 in 20 titles is UNVERIFIABLE).

## Whisperai results: what to expect (Jay, 2026-11-16)
Black Panther (whisperai) reviewed by Jay: in sync, with gaps on made-up words and names. Expected: whisperai transcribes the audio, so timing
is right but invented names are not. Every whisperai replacement post now says so. Idea parked, not built: re-check whisperai-fixed titles every
few months, and swap in a community sub if one has appeared that passes rule v1 (names are usually right in a human-made sub).
Jay's overall impression after the first batches: about 98% of what he has watched is in good shape.
