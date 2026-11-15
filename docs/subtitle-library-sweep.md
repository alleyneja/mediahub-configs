# Subtitle Library Sweep: Decision Record

**Status:** APPROVED 2026-11-15 (all five items confirmed by Jay, section 6). NOTHING RUN YET; Phase 1 is blocked on the parent record's benchmark bars.
**Issue:** alleyneja/mediahub-issues#73
**Parent record:** [subtitle-quality-gate.md](subtitle-quality-gate.md). That record scoped v1 to new downloads
and left a library sweep as "a separate later decision". This is that decision.
**Challenge depth:** FULL (unattended edits to many files at once; PROCESS.md "The challenge step")

## Problem

The Nov 13 audit flagged about 10% of 15,146 synced English subs (1,551 flagged by pegged offset / scale /
short / over-run, plus 1,666 more at 10-85 s needing review). The gate in the parent record stops the problem
growing; it fixes none of the existing damage. Jay's request: get the existing bad subs replaced, but in small
batches so that a mistake is a small one.

## 1. Requirements (proposed, confirm in section 7)

**Must do:** find the bad English subs in the library; replace them with a sub that measures better; report
what happened to every file.

**May touch:** English subtitle files for titles in the current tranche; Bazarr blacklist and history for
those titles; a results ledger and backups under `~/subtitle-sweep/`.

**Must never (inherited from the parent record, all still apply):**
- remove a sub before a better one passes (download-then-swap only)
- leave a title worse than before (swap only if the candidate measures strictly better)
- change global Bazarr settings
- touch the Spanish files
- run while Plex has an active stream, or outside the off-peak window

**Added here:**
- one tranche at a time; the next tranche does not start until the previous one is reviewed by Jay
- every replaced file has its original kept in a dated backup that restores with one command
- a hard stop: any title ending worse than before halts the sweep until reviewed
- fail open: a crash or hang leaves files as they were

## 2. Options considered

| Option | Verdict |
|---|---|
| A. Delete all flagged subs, let Bazarr re-search | Rejected. Replacement is unverified (Bazarr scores metadata, not content); batch 1 hand-replacement passed 7 of 14 under the strict rule; blacklisted candidates run out on titles where every community sub is timed to another cut; delete-first lets the scheduled search refill the slot unpredictably. |
| B. Detect-only pass, then replace in tranches, download-then-swap, strictly-better rule, whisperai fallback | **Chosen.** |
| C. Alert only; Jay fixes titles by hand | Fallback if tranches show the replacement rate is too low. |

## 3. Design (option B)

**Phase 1: detect-only (read-only).**
- Candidate list from the free DB-only report (`sync_report.py`): the 1,551 flagged plus the 1,666 to review.
- Measure candidates with our 7-window audio measurer (the gate's measurer, after it passes the parent
  record's section 3 bars). Talkarr, run read-only, is a second opinion: on the Nov 15 backup test it flagged
  13 of 14 bad files but missed slow drift (Sith) and false-flagged 2 of 8 good ones, so it corroborates and
  never acts alone.
- Rule: "both signals agree" = high confidence; "only ours" = medium (drift cases); "only Talkarr" = review.
- Cost: about 30 s per file at the Nov 15 rates, so roughly 27 hours of low-priority CPU for about 3,200
  candidates. Spread over off-peak windows, one file at a time, NAS thread count watched. Not the full 15,146.
- Output: a ranked list written to the ledger. No file changes.

**Phase 2: tranches.** Order within the list: titles Jay watches first (source to be confirmed, section 7),
then both-signals-agree, then the rest.

| Tranche | Size | Notes |
|---|---|---|
| T0 pilot | 5 titles | Jay reviews every file by eye in Plex |
| T1 | 10 | |
| T2 | 25 | |
| T3 | 50 | |
| T4+ | 100 per night, capped | only after T3 is clean |

Per title: fetch candidates through Bazarr's manual-search API (never PATCH search), download to a temp path,
measure, swap only if strictly better than the current file; up to 3 candidates, then whisperai; if none
passes, keep the current file and flag the title "unresolved".

**Between tranches (gate to proceed):**
- zero titles ended worse than before (guaranteed by construction; verified from the ledger)
- replacement rate reported; proposed bar is at least 50% resolved in T0-T1 (batch 1 baseline is 7 of 14
  under a strict rule), at least 70% afterwards; below the bar means stop and review, not push on
- unresolved titles listed for a decision (whisperai only, manual, or leave)
- Jay confirms in the issue before the next tranche

## 4. Challenge outcome

**Benchmark.** Staged rollout: Google SRE canarying / gradual rollout guidance (small first tranche, review,
widen). We differ by gating on Jay's review rather than a time window, because the signal is subjective
("looks in sync in Plex"). (Citation not re-verified in this session.)

**Devil's advocate (does not win, changes the plan).**
1. Replacement may mostly fail on exactly the worst titles; the sweep could produce a long unresolved list.
   Accepted: unresolved keeps the current file, so nothing is lost; it also tells us which titles need whisperai
   or manual work.
2. The measurer has not yet been proven on the benchmark. Therefore Phase 1 does not start until the parent
   record's section 3 bars are met.
3. Is this worth doing at all, with 90% of subs fine? Answer: the damaged titles include the ones Jay watches
   (Sopranos, Sith), and the cost of a wrong tranche is small by design.

**Stress test.**

| Scenario | Required behaviour |
|---|---|
| NAS / NFS unreachable | cannot measure; skip, retry; never swap on a failed read |
| Bazarr down mid-tranche | temp file discarded, current sub untouched, tranche pauses |
| Power loss mid-swap | atomic rename; backup exists; leftover temp files ignored |
| Plex stream starts | the job stops at the next file boundary |
| Disk full | backup write fails, swap aborted |
| Two copies of the sweep run | lock file; second exits |
| whisperai rate-limited | title marked unresolved, retried in a later tranche |
| Jay away two weeks | no tranche starts without his confirmation, so the sweep simply waits |
| Bazarr upgrade cycle replaces a swapped sub | upgrades are covered by the gate in the parent record |

**Pre-mortem (six months on, this failed).**
1. Good subs were swapped for subjectively worse ones (the measurer only checks timing): spot-check by eye in
   T0-T2; originals kept 90 days.
2. Blacklist growth emptied candidate pools: per-title cap on blacklist entries.
3. NAS or Plex load caused complaints: off-peak only, one file at a time, stop on active streams.
4. The sweep silently stalled: Kuma heartbeat and a ledger summary after each night.
5. Bazarr changed behaviour mid-sweep: pin version; canary known-good and known-bad files at the start of each
   tranche.

**Risks accepted in writing:** some wrong-cut subs will pass; some titles stay unresolved; Plex needs a rescan
for swapped titles to show the new sub.

## 5. Rollback

Each swap writes the original to `~/subtitle-sweep/backups/<date>/<title>/`. A restore script copies it back
and removes the Bazarr blacklist entry. Rollback of a whole tranche is a loop over its ledger rows.

## 6. Decision log

Confirmed by Jay, 2026-11-15:
1. Watch list = Plex watch history on Jay's account (from r9).
2. Per-tranche replacement bar: at least 50% resolved in the early tranches (T0-T1), at least 70% after.
3. Run window = the existing Plex-aware maintenance windows (Tue / Thu / Sat).
4. Spanish stays untouched in the sweep (no extra variable).

5. Tranche sizes 5 / 10 / 25 / 50, then 100 a night: confirmed.

## 7. Open items for Jay

1. ~~Watch-list source~~ Confirmed: Plex watch history on Jay's account.
2. ~~Tranche sizes~~ Confirmed: 5 / 10 / 25 / 50, then 100 a night.
3. ~~Replacement bar~~ Confirmed: 50% early, 70% after.
4. ~~Run window~~ Confirmed: Tue / Thu / Sat maintenance windows.
5. ~~Spanish~~ Confirmed: untouched.
