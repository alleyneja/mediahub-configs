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
