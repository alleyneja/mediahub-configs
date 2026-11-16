# Subtitle Gate: Enforce Mode (auto-replace) Decision Record

**Status:** DRAFT 2026-11-16, waiting for Jay's go. Nothing in this record is live. The cron job stays report-only until Jay approves.
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

## Not in scope
TV episodes, the Spanish files, the library sweep (still manual, in tranches, with Jay's confirmation in #73), the tail probe for credit-heavy films (planned in the parent record, section 10).

## Risks and what is not known
- The precision of rule v1 is measured on a small sample. The spot-check and the fall-back to report-only are the control.
- Whisperai output can mangle names and slang. It still has to pass rule v1, but rule v1 checks timing, not wording.
- Bazarr's own scheduled search can refill an empty English slot mid-run (seen once). Mitigated for Tier B by never emptying the slot.
- Candidate supply: some titles have no passing community sub. They end as whisperai or "unresolved".

## Rollback
Remove the wrapper line from cron (or create the PAUSE file). The gate itself keeps running report-only. Replaced files restore from `~/subtitle-sweep/backups/`.

## Open items before go-live
- Wrapper script, Discord post, spot-check marker, audio-stream guard: to be built and tested on files nothing runs yet.
- Kuma heartbeat for the wrapper (Jay's call; needs a Kuma stop, see `gotcha_kuma_monitor_db_insert`).
- Jay's approval of this record.
