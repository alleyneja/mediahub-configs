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
   53 scaled files) is a separate later decision.
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

## 6. Next steps

1. Build the benchmark set and run the measurer against it (read-only).
2. Compare against the section 3 bars; set the cutoff from data.
3. If the bars are met, build the report-only gate (runs from i7 against Bazarr's DB and API).
4. Run report-only until the section 4 exit; then a separate decision to enforce.

Related tooling: `~/subtitle-audit-tools/` (audio_windows.py, pgs_vs_srt.py, sync_report.py).
