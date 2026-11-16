# Subtitle Replace Tool and T0 Pilot: Decision Record

**Status:** DRAFT 2026-11-15, waiting for Jay's go. NOTHING RUN. No subtitle file touched.
**Issue:** alleyneja/mediahub-issues#73. **Parents:** [subtitle-quality-gate.md](subtitle-quality-gate.md), [subtitle-library-sweep.md](subtitle-library-sweep.md)
**Challenge depth:** FULL (changes files). The sweep record's challenge (stress test, pre-mortem) applies; this record adds what is new.

## Decision (Jay, 2026-11-15)
Build `scripts/subtitle-replace.py` and run it by hand on the 5 titles Jay confirmed bad by eye (the sweep's T0).
Cron automation comes after T0 is reviewed. Talkarr is not used to fix or replace: its "replace" is delete +
Bazarr blacklist + unchecked refill, and its fixes were unreliable (gate record, section 6 and the Nov 15 trial).
Bazarr is the candidate source; our measurer (rule v1) is the judge.

## Tiered rule (changes one inherited must-never, by Jay's decision)
The inherited rule "remove a sub before a better one passes" is relaxed ONLY for useless subs:
- **Tier A, useless** (last cue before 50% of runtime, or fewer than 20 cues): delete up front, then try up to 3
  candidates, then whisperai. If none passes, the title is "unresolved" and has no English sub (it effectively had
  none before). Boy Next Door (28%) and Maid in Manhattan (9 cues) are Tier A.
- **Tier B, playable but off** (everything else FAIL): download-then-swap. Swap only if the candidate passes rule v1
  and is strictly better than the current file; otherwise keep the current file. Hoot, Delivery Man and Fired Up!
  are Tier B.
- **UNVERIFIABLE** and FAILs whose only reason is the 90% last-cue check: reported, never touched.
- Still never: change global Bazarr settings, touch Spanish, run during a Plex stream, leave a Tier B title worse.

## Procedure per title
1. Lock; Plex idle check before every candidate; stop on any stream or unknown.
2. Back up the current English sub to `~/subtitle-sweep/backups/<date>/<title>/` (Tier A too, even if useless).
3. Candidates from Bazarr manual search (`GET /providers/{movies|episodes}`), never PATCH search. Skip duplicates
   (same uploader and identical timing), skip anything already blacklisted for this title.
4. Bazarr's download call has no temp path (batch-1 `attempt.py` shows it writes straight to the library path). So: Tier B snapshots the current file first, lets Bazarr write the candidate, waits for Bazarr's own sync to finish, measures it, and puts the snapshot back unless the candidate passes rule v1. Judge with rule v1.
5. Pass (Tier B: and strictly better) -> atomic rename into place with the profile filename (plain `.en.srt`).
   Blacklist the rejected candidates in Bazarr and verify by DB row and file absence (not HTTP status).
6. After 3 failed candidates -> whisperai once. Still failing -> unresolved, current file kept (Tier B) or none (Tier A).
7. Blacklist cap per title (3 entries per run). Write a ledger row for every attempt.

## T0 scope and success
Titles: Delivery Man (2013), Fired Up! (2009), Hoot (2006), The Boy Next Door (2015), Maid in Manhattan (2002).
Report per title: tier, candidates tried, outcome, before/after measurements, backup path. Jay watches each in Plex
after a library rescan. Bar from the sweep record: at least 50% resolved (3 of 5). Not met = stop and review.
Rollback: `subtitle-replace.py --restore <title>` copies the backup back and removes the blacklist rows.

## Stress points specific to this tool
- Bazarr's download writes straight to the final path (confirmed from batch-1 code, to be seen on the first real run, Maid in Manhattan, which is Tier A and so risks nothing). Tier B therefore has a short window where the candidate is in place; it is restored from the snapshot if it fails. A Plex scan in that window could briefly see the candidate (the pilot runs only while Plex is idle).
- Blacklisting a rejected Tier B candidate uses a non-existent path (`<path>.rejected`) so the API adds the row without deleting the restored file. Verified by DB row on the first run; if it does not work the blacklist step is skipped, not forced.
- `--restore` copies the backup back; blacklist rows are left in place (they only stop the same sub returning).
- Bazarr's scheduled missing-search may refill a Tier A slot mid-run: the tool re-checks the slot before swapping.
- A candidate that is a hearing-impaired `.en.hi.srt` is renamed to the profile filename.
- Whisperai rate limit or timeout: title marked unresolved, retried in a later tranche.

## Not decided here
Cron automation, the tail probe for credit-heavy films, the Kuma heartbeat, and library-wide tranches beyond T0.
