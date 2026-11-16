# Subtitle Quality Gate: Decision Record

**Status:** requirements and design approved 2026-11-15; NOTHING BUILT YET
**Issue:** alleyneja/mediahub-issues#73
**Challenge depth:** FULL (unattended changes to files; PROCESS.md "The challenge step")

## Problem

Bazarr scores subtitle candidates on release metadata, never on content or timing (a truncated
53-cue file scored 94%). Its sync step applies one shift and nothing rejects a bad result. The Nov 13
audit flagged about 10% of 15,146 synced subs (a floor: it caught 4 of 5 known-bad files). Some
community subs are timed to a different cut of the film. Batch 1 manual repair passed 7 of 14, below
its declared bar.

## 1. Requirements

**Must catch:** pegged offset, framerate scale, truncated or overrunning sub, different-cut content.

**May touch:** Bazarr blacklist and history; English subtitle files for rejected downloads.

**Must never:**
- remove a sub before a better one passes
- change global Bazarr settings
- touch the Spanish translated files
- leave a title worse than before

**Added by the challenge:**
- fail open: a dead or hung gate means today's behaviour, never blocked downloads
- a backup of every file before it is swapped
- swap only if the new candidate measures strictly better than the current one
- never measure while Plex has an active stream; run off-peak
- never reject on a failed read (NAS or NFS unreachable means "cannot measure", retry later)
- gate Bazarr upgrades too, not only first downloads
- one writer only (i7), idempotent per file, with a lock
- cap blacklist entries per title

## 2. Decisions (Jay, 2026-11-15)

1. **On failure (after report-only):** auto-replace. Download-then-swap, next candidate up to 3, then
   whisperai. Every action reported to Discord.
2. **If nothing passes:** keep the best available sub and flag the title "unresolved". Never remove.
3. **Scope v1:** new downloads only (including upgrades). Library sweep (1,551 flagged, 440 damaged,
   53 scaled files) is a separate decision: see [subtitle-library-sweep.md](subtitle-library-sweep.md) (draft).
4. **Spanish:** leave alone. Gate is English-only in v1; note which Spanish files derive from any
   replaced English one.

## 3. Pre-build: measurer benchmark (new)

The gate acts on a measurement of unproven accuracy, so the measurer is validated first.

- **Known-good set:** titles where the embedded track AND the audio measurement (ffsubsync, 7 windows)
  agree. Embedded alone is not truth (RE2026's embedded tracks were wrong); two independent signals
  agreeing is.
- **Synthetic known-bad:** controlled corruption of known-good subs: shifts of 1, 5, 20, 60 s;
  framerate scale (23.976 to 25); truncation to 50% and 90%; spliced or cut middle section; growing drift.
- **Real known-bad:** Sith, Uncharted, RE2026, Get Hard, and the 4 of 5 caught earlier.
  Real known-good: the 5 Sopranos.
- **Blind spot, accepted:** different-cut content is covered only by the few real cases.
- **Acceptance bars, fixed BEFORE results are seen:**
  - false positives on the untouched good set: no more than 2%
  - recall on shifts over 5 s: at least 90%
  - per-failure-type recall and the low-speech-window behaviour are reported
  - the cutoff (currently a guessed ~10 s usable) is set from the data
- The gate is not built until the measurer meets the bars. If it cannot, the plan reverts to
  alert-only (decision 1 fallback).

## 4. Report-only phase

The gate measures every new download and reports, changing nothing. Exit is count-based, not time-based:
at least 30 gated downloads, every flag manually reviewed, precision above the bar from section 3.
Then enforce.

## 5. Challenge outcome

**Benchmark.**
- Staged rollout: Google SRE canarying / gradual rollout guidance. We differ by using a count-based
  exit because download volume is low.
- Vendor-supported surface: Bazarr's own sync (ffsubsync, max-offset, golden-section search,
  framerate fix). It has no content validation, which is the gap this fills. We use only the manual
  search and download API plus the blacklist; never PATCH search.
- Professional timed-text guidance (Netflix timed-text style guide, BBC subtitle guidelines) treats
  sync as a quality criterion. We use a looser threshold because our measurement is noisy.
- (Citations not re-verified against the sources in this session; check before relying on them.)

**Devil's advocate (did not win, but forced changes).** Arguments: the measurer is the weakest part
(low-speech windows read +/-23-60 s; no false-positive rate known); the whisperai fallback failed
Sith and Uncharted; Bazarr replace hazards have already bitten us; new-downloads-only fixes none of
the damage Jay noticed; ffsubsync load could compete with Plex. Outcome: the benchmark (section 3),
a tested whisperai path, and off-peak / no-active-stream scheduling became requirements.

**Stress test.**

| Scenario | Required behaviour |
|---|---|
| NAS or NFS unreachable | cannot measure; skip, retry; never reject |
| Bazarr restarting mid-swap | download-then-swap; old file kept until the new one is verified |
| Power loss mid-swap | backup exists; temp `.synced.srt` never mistaken for a result |
| Gate crashes or hangs | fail open |
| Disk full | backup fails, so abort swap, old sub stays |
| Bazarr 6h upgrade replaces a passing sub | upgrades gated; never leave a title worse |
| Two machines act | single writer (i7) with lock |
| whisperai rate-limited | mark unresolved, keep best, retry later |
| Jay away two weeks | Discord reports, Kuma heartbeat, nothing destructive accumulates |
| Few downloads in a week | count-based exit |

**Pre-mortem and mitigations.**
1. False positives replaced good subs: backups, strictly-better swaps, before/after in every report.
2. Bazarr update changed behaviour: known-good and known-bad canary files checked regularly, alert on disagreement.
3. Gate died silently: Kuma heartbeat plus weekly count in the digest.
4. Blacklist growth left titles empty: never remove without a passing replacement; per-title cap.
5. Plex stutter from measuring: off-peak and no-active-stream rule.

**Risks accepted in writing:** some different-cut subs will pass (content-level detection is
imperfect); old library damage remains until a separate sweep decision.

## 6. Prior-art survey (added 2026-11-15)

Done before building, to avoid re-inventing the wheel. Web searches plus `gh search repos`; not
exhaustive. Findings come from READMEs, release notes and package pages, not from running the tools.

| Project | Relevant capability | Why not adopt as-is |
|---|---|---|
| ffsubsync, alass | sync + offset/framerate report; ffsubsync has `--min-score` / `--skip-sync-on-low-quality` | sync tools, not a gate; ffsubsync already in use |
| Bazarr+ fork (LavX/bazarr v2.6.0) | max offset becomes an acceptance threshold; rejected result falls back to next engine | replaces core Bazarr; offset case only; no truncation/wrong-cut/framerate rejection |
| subflux (cplieger) | 0-1 sync confidence (apply at 0.5, auto-apply 0.6); candidate voting (agree if per-cue diff < 1500 ms); known-ratio + golden-section framerate; split alignment; golden-corpus validation | `internal/` Go package, cannot import; re-aligns instead of rejecting |
| Talkarr (Nick2253/Talkarr) | Bazarr post-processing webhook (`POST /process`); validates cue density / duration sanity / mislabelled forced subs; blacklists via Bazarr API and re-searches; Manual / Safe Auto / Full Auto modes; backs up before change; **perturbation eval** (`talkarr/eval/perturb.py`: known shifts, framerate scales, dropped cues, junk cues, wrong-film sub with expected verdicts) | 0 stars, created 2026-10-16, no license (cannot copy code), one maintainer. Validation of regular subs is structural, not audio timing. Its docs say to disable Bazarr's own auto-sync (violates "never change global settings"); `MISLABELLED_ACTION=remove` default removes without backup (violates "never remove first") |
| Subarr (coaxk/subarr) | quality flags on generated subs; manual blacklist | different job |
| Sublarr (Abrechen2/sublarr) | "sync-quality" scoring in candidate scoring | how it is measured is not documented |
| btch/bazarr-qc | reported by a search result: blacklists when recorded offset > 5 s | repo not found on GitHub (user `btch` has only Sportarr); unverified, and it would only read Bazarr's recorded offset |
| ahmadmu.com blog | accepts a shift only when an English reference and the audio agree within 0.5 s across each third of the video | write-up only, no code |

**Conclusion:** the measuring primitives exist (ffsubsync, alass) and several projects solve parts of the
problem, but nothing found does audio-timed rejection of truncation / wrong cut / framerate with
automatic download-then-swap, blacklist, backup, report-only phase and an unresolved flag. We build that
glue and borrow ideas, not code.

**Ideas adopted:**
- Perturbation benchmark with expected verdicts (Talkarr; matches section 3).
- Candidate agreement within 1.5 s as a cheap extra signal and a duplicate-candidate detector (subflux).
- A 0-1 confidence with separate apply and auto-apply thresholds (subflux).
- Reference agreement across thirds of the runtime, English reference vs audio (ahmadmu blog).
- Manual / Safe Auto / Full Auto modes with backup-first changes (Talkarr). Our report-only week is "Manual".

**Survey step before building:** time-boxed (about an hour) read of the Bazarr+ fork and Talkarr code to
confirm none already meets the section 3 bars. If one does, adopt it instead of building the measurer.

## 7. Next steps

1. Time-boxed code read of Bazarr+ rejection logic and Talkarr validation/eval (section 6).
2. Build the benchmark set and run the measurer against it (read-only).
3. Compare against the section 3 bars; set the cutoff from data.
4. If the bars are met, build the report-only gate (runs from i7 against Bazarr's DB and API).
5. Run report-only until the section 4 exit; then a separate decision to enforce.

Related tooling: `~/subtitle-audit-tools/` (audio_windows.py, pgs_vs_srt.py, sync_report.py).

## 8. Measurer benchmark results (2026-11-15)

Scripts: `scripts/subtitle-gate-benchmark/`. Method as designed in section 3, with one change: the
"known-good" label needed two independent signals to agree (our 7-window audio measurement within 0.5 s,
AND Talkarr reporting nothing; for the 13 Sopranos episodes the embedded PGS track also agrees within
+/-0.25 s). That gave **16 known-good subs**: 13 Sopranos S01 episodes, Jumanji, I Am Legend, Resident Evil
(2026). Sith, Get Hard and Uncharted were left out as ambiguous. Each good sub was corrupted 17 ways
(shifts of +/-1/5/20/60 s, three framerate scales, truncation to 50% and 90%, a 10-minute gap, a +45 s step at
45%, linear drift to +4 s and +8 s), 288 measurements. The rule was tuned on 8 titles and tested on the other 8.

**Rule v1** (`chosen_rule_v1.json`) is three-state: FAIL = positive evidence of a mismatch; UNVERIFIABLE = too
few usable windows and nothing contradicts (report, never replace); PASS. Usable window = reads within 10 s;
FAIL if fewer than 7 windows have 10+ cues, last cue ends before 90% of the runtime, any usable window
reads more than 0.5 s, usable windows spread more than 1.0 s, or 3+ windows agree on the same large offset.
PASS needs at least 5 usable windows.

| Bar / category | Result |
|---|---|
| False positives on untouched good subs (bar: no more than 2%) | 0 of 16 observed |
| Recall on shifts of 5 s or more (bar: at least 90%) | 96 of 96 (100%) |
| Shifts of 1 s | 32 of 32 |
| Framerate scales | 34 of 48 FAIL, 14 UNVERIFIABLE (the small x1.001 scale), 0 silently passed |
| Truncation | 32 of 32 (but see finding 2) |
| Step (different cut) | 16 of 16 |
| Drift | 32 of 32 |
| 10-minute gap | 12 of 16, 4 silently passed |

**Real known-bad (Nov 13 backups, both signals agree):** all 13 FAIL. Get Hard's original and one Uncharted
candidate came out UNVERIFIABLE (flagged for review, not passed). Sith's current file is UNVERIFIABLE
(only 3 usable windows, all reading 0.0 to +0.1 s).

**Findings (honest limits).**
1. **The 2% false-positive bar cannot be demonstrated at this size.** 0 of 16 gives a 95% upper bound of
   about 19%; proving 2% needs roughly 150 clean files. The 13 Sopranos episodes are also one show. Treat
   0 of 16 as "no evidence of a problem", and measure precision live during the report-only phase.
2. **Truncation detection leans on a fragile threshold.** Without the 90% coverage check, 13 of 32
   truncations pass. I Am Legend's good sub ends at 91% of the runtime, so the threshold sits 0.01 above a
   known-good file; a film with long credits would be falsely failed. Needs a per-file speech-end estimate
   from the audio instead of a fixed ratio.
3. **A missing block of dialogue between sample windows is invisible** (4 of 16 gaps passed). A sparse-window
   check caught one more but failed Sith's current sub, so it was dropped.
4. **Low-speech films** (Sith: 4 of 7 windows read as artifacts) land in UNVERIFIABLE. That is the right
   outcome, but it means such titles can never be confirmed good with 7 windows. More windows or a
   speech-weighted window choice is a possible later improvement.
5. Synthetic corruptions are easier than real ones. The real known-bad set (13 of 13) is circular for
   labels (our own measurer plus Talkarr), so it supports the rule but does not independently prove it.

**Bars status:** recall bar met; false-positive bar not provable at this n (see finding 1). Jay's call whether
v1 is good enough to start building the report-only gate; findings 2 to 4 are known gaps, not blockers.

## 9. Report-only gate: built (2026-11-15)

`scripts/subtitle-gate.py` on prod (i7), cron Tue/Thu/Sat 04:45 (after the 02:00-04:30 maintenance window), rule v1
from `scripts/subtitle-gate-benchmark/chosen_rule_v1.json`. **It changes nothing**: no subtitle file is edited,
deleted, renamed or blacklisted, and no Bazarr setting is touched. It reads Bazarr only through a read-only snapshot of
its history database, so no Bazarr post-processing hook is needed (that would have been a global Bazarr setting).

What a run does: finds English (regular and HI) downloads and upgrades since its checkpoint, waits until the file has
been untouched for 10 minutes (Bazarr's post-download sync rewrites it), measures it in the bazarr container at lowest
CPU priority, applies rule v1 and appends to `~/logs/subtitle-gate/ledger.jsonl`. It posts to #media-alerts only when
something is flagged. Safety: stops when Plex has a stream or the count is unknown (checked before every batch of 5),
2-hour cap, single instance (lock), refuses a suspicious backlog (over 200 files), and fails open (any error ends the
run with files untouched). First run starts from "now"; `--backfill-days N` looks back.

Operating it:
- `subtitle-gate.py --summary`: counts, every flagged file with the reason, review progress, and whether the
  report-only exit is met (30+ gated downloads and every flag reviewed by eye).
- `subtitle-gate.py --review "<part of the name>" good|bad|unsure`: record your verdict on a flagged file. That is
  what turns the 0-of-16 false-positive observation into a live precision number.
- `subtitle-gate.py --dry-run`: what the next run would measure.
- Tests: `python3 -m unittest scripts/test_subtitle_gate.py`.

First run (Nov 15, 5 of 10 queued files before a viewer started a stream and the guard stopped it): 2 PASS, 3 FAIL.
The failures read like real problems: Delivery Man drifts smoothly +1.6 s to -3.5 s (framerate-style), Fired Up! is
fine until the last two windows (-18 s, -14 s), Hoot wanders +/-2 s throughout. Not yet reviewed by eye.

Known gaps: no Kuma heartbeat yet (creating a push monitor means stopping Kuma briefly; Jay's call), the three
limits in section 8 (truncation threshold, gaps between windows, low-speech films), and nothing yet acts on a FAIL:
replacement is the next phase, only after the report-only exit.


## 10. First eyeball reviews and the coverage-threshold question (2026-11-15)

Jay reviewed all 6 flags from the first 10 gated downloads in Plex: 5 confirmed bad, 1 false positive (live
precision 5 of 6; far too few to test the 2% bar).

| Title | Flag | Jay's verdict |
|---|---|---|
| Delivery Man | smooth drift +1.6 to -3.5 s | bad |
| Fired Up! | last windows -18 s / -14 s | bad |
| Hoot | wanders +/-2 s (hard to see by eye; he likely watched in-sync stretches) | bad |
| The Boy Next Door | sub stops at 28% of runtime, nothing after | bad (he saw the cut-off line) |
| Maid in Manhattan | sub is a 9-cue fragment (492 bytes, all at 31:29-31:49) | bad (he saw no English subs at all) |
| Home (2015) | only reason: last cue at 90% of runtime (long end credits) | good = false positive |

Both truncated files arrived as ordinary Bazarr downloads scored 162 and 133: the case the gate exists for.

**Considered: relax the coverage check from 0.9 to 0.7 (rule v1.1) so credit-heavy films pass.** Re-scored on the
saved benchmark measurements (`scripts/subtitle-gate-benchmark/rescore_coverage.py`, no re-measurement). Result:
false positives stay 0 of 16, but the "sub cut at 90%" truncation test falls from 16/16 caught to 3/16, because
the sampled windows sit earlier in the film and only the last-cue check sees a late cut. Home's last cue is at
89.6%, so last-cue position alone cannot tell long credits from a sub missing its final 10% of dialogue.
**Decision: rule stays v1.** Not shipped, because it would trade a known false positive for missed real truncation,
which is a must-catch (section 1).

**Planned instead (not built):**
1. A tail probe: check the audio after the last cue for speech; credits have none, a truncated sub has dialogue.
   This needs new measurement code and a re-run of the benchmark with the bars unchanged.
2. Until then, in enforce mode a FAIL whose only reason is last-cue coverage is routed to "needs review", never
   auto-replaced (same treatment as UNVERIFIABLE). Both truncated files above had other reasons too.

**Review policy (Jay, 2026-11-15):** review every flag until the gate has seen 30 downloads; after that enforce
mode replaces flagged subs automatically and Jay spot-checks a sample of the swaps; the first library-sweep
tranches are still reviewed in full (sweep record); UNVERIFIABLE is reported and never replaced.
