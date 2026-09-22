# Fleet Permissions Monitor

Operational guide for the alert-first permissions monitor built under D12
(`~/mediahub-cleanup.md`) to catch the recurring mode-000 / mode-777 anomaly pattern
across the whole `/mnt/media` pool, on both production and r9. This monitor **never
auto-fixes** — it detects, logs, and queues. Root cause of the underlying anomaly
pattern is still not found; this guardrail only protects the library while that
investigation continues.

## What runs where

| | Production | r9 |
|---|---|---|
| Cron | `*/15 * * * * /home/jay/mediahub-configs/scripts/permissions-monitor.sh` | `*/15 * * * * /home/jay/scripts/permissions-monitor.sh` |
| Scripts deployed from | `~/mediahub-configs/scripts/` (repo checkout in place) | `~/scripts/` — **plain copies, not a repo checkout** |
| Scanned root | `/mnt/media` | `/mnt/media` |
| State (already-seen files) | `/home/jay/logs/permissions-monitor-seen.txt` | same path, separate file |
| Forensic log | `/home/jay/logs/permissions-forensics.log` | same path, separate file |
| Queued-but-unfixed paths | `/home/jay/logs/permissions-pending-fixes.txt` | same path, separate file |
| Discord alerting | `scripts/lib-permissions-alert.sh` + `scripts/permissions-alerts.env` (gitignored, webhook URL) | same files, separately populated |

**Deployment asymmetry to remember:** r9 does *not* have a full `mediahub-configs` git
checkout — `/home/jay/mediahub-configs/` exists there but only holds an unrelated
`stacks/` subtree. The permissions-monitor family on r9 lives at `/home/jay/scripts/`
as manually-copied files, confirmed byte-identical to the repo's `scripts/` versions as
of 2026-09-22. This means **a future edit to any script in this family in the repo will
not reach r9 automatically** — it has to be copied over by hand. This is the same class
of drift risk as `gotcha_repo_ahead_of_live_file` and `gotcha_caddyfile_live_vs_mirror`;
check both copies before assuming a fix is live on r9.

The two hosts' state/log/pending files are independent — there is no shared state.
Anomalies found on production and anomalies found on r9 are two separate queues,
separate logs, and (as of Task 8's r9 first-run) can require more than one scan pass to
fully enumerate (see "r9's first scan" below).

## How to review and approve a fix

The monitor only ever appends to the forensic log and the pending-fixes file. Nothing
is chmod'd until a human runs the fix script deliberately.

1. Review what's queued:
   ```
   cat /home/jay/logs/permissions-forensics.log      # full detail per anomaly: stat, mergerfs branch, container states, NAS log snapshot
   cat /home/jay/logs/permissions-pending-fixes.txt  # flat list of paths still queued
   ```
2. Approve and apply. `permissions-apply-fix.sh` is the *only* script in this family
   that runs `chmod`, and it chmods to `644`.
   ```
   /home/jay/mediahub-configs/scripts/permissions-apply-fix.sh                 # fixes EVERYTHING currently queued
   /home/jay/mediahub-configs/scripts/permissions-apply-fix.sh /path/to/one/file  # fixes only the path(s) given, leaves the rest queued
   ```

**Do not run it bare against the real backlog.** As of 2026-09-22 there are
9,277 real anomalies queued on production and ~8,832 on r9 (see below) that Jay has
deliberately chosen not to bulk-fix yet — he wants to review the composition further
first. Any time you want to demonstrate or test the fix path (e.g. against a synthetic
file from `permissions-repro-test.sh`), pass its exact path as an explicit argument,
never run the script with no arguments, exactly as Tasks 5 and 7 did.

### Real backlog composition (production, snapshotted 2026-09-22)

9,277 real anomalies, by category (column totals below sum exactly to 9,277):

| Category | Total | @ mode 000 | @ mode 777 |
|---|---|---|---|
| podcasts | 5,204 | 1,420 | 3,784 |
| photos | 2,581 | 2,058 | 523 |
| games | 831 | 790 | 41 |
| downloads | 563 | 557 | 5 (+ 1, see note) |
| audiobooks | 52 | 52 | 0 |
| tv | 27 | 1 | 26 |
| ebooks | 18 | 0 | 18 |
| music | 1 | 0 | 1 |

**Downloads note:** the monitor queued 563 downloads anomalies at detection time, but
by the time of the manual `stat` check moments later, one of them had already been
deleted or moved (its `stat` came back with an empty mode) — leaving 557 @ 000 and 5 @
777 confirmed-bad right now, plus that 1 already-resolved/gone file. The 563 total
reflects what was queued at the snapshot moment, not a fourth anomaly class; the table's
`@ mode` columns only cover the 562 still confirmably bad.

The `photos` count is the real, live Immich library (`/mnt/media/photos/immich` and
`/mnt/media/photos/immich-originals-mirror`) — not a stray or dead corner of the pool.
**Open, not-yet-investigated flag:** Immich itself may be silently treating some of
these photos as missing from its own point of view, the same pattern seen in the Plex
r9 incident (`docs/plex-r9-missing-content-incident.md`). Not confirmed — just noted so
a future session checks Immich's own "missing file" state against this list before
assuming the pool-level anomaly is the whole story.

### r9's own view: two scan passes needed

r9 recorded 8,832 real anomalies — but getting a *complete* count took two runs of
`permissions-monitor.sh`, not one. r9's `find` over its NFS-backed pool didn't fully
traverse the tree on the first pass (this is a real operational quirk of scanning that
mount, not a bug that was fixed in the script). **If the monitor is run for the first
time on a new host again, don't assume the first run's count is the complete picture —
run it a second time and compare.**

## Manual repro steps (D12 Section 2) — need Jay's hands

`permissions-repro-test.sh` (this task) automates the same-host and cross-host plain
writes into `/mnt/media/.permissions-repro-test/`. It cannot drive these two, which
need to happen interactively:

1. **SMB copy from the gaming PC.** Copy a file from the gaming PC, over the network
   share, into `/mnt/media/.permissions-repro-test/`. Then immediately run
   `permissions-monitor.sh` on both hosts and check
   `/home/jay/logs/permissions-forensics.log` for anything new.
2. **A UGOS app action.** From the NAS's own web UI, trigger an app-level action against
   that same folder — e.g. Media Server's re-index on
   `/mnt/media/.permissions-repro-test/` (mapped to whatever share path UGOS shows it
   under). Then run `permissions-monitor.sh` on both hosts right after and check the
   forensic log the same way.

Do both, one at a time, so a hit can be attributed to the specific action rather than
lumped together.

### New lead from Task 8's own repro run (2026-09-22) — 777 on creation, independent of umask

Running `permissions-repro-test.sh`'s plain writes today produced **777 on both
production and r9**, on the very first write (a plain `echo ... > file` from a bash
shell with `umask` reporting `0002`, which should produce `664`, and on r9 with the
identical shell redirect over SSH). Both landed on the `/mnt/nas` mergerfs branch. This
lines up with — and now extends — a previously-unconfirmed lead: a plain `touch` on r9's
`/mnt/media` mount had already been seen landing at 777 despite the session's umask.
Today's run reproduces the same result via a different write method (shell redirect,
not `touch`) and on a second host (production), always on the `/mnt/nas` branch.

This is now three data points (r9 touch, production redirect, r9 redirect-over-SSH),
all on the same mergerfs branch, all ignoring the process umask — consistent with the
mount itself (rather than any specific writer) forcing new files to 777 on creation.
Per the evidence standard, three same-direction data points on one branch is
corroboration, not proof — mergerfs picks branches by free space at write time, so this
hasn't been tested against the other branch (the local ext4 disk) to see if it behaves
the same way. **This is a concrete, testable lead for a future session, and it only
covers the 777-anomaly class** — it does not explain how any file ends up at mode 000.

### D12 hypothesis update: r9's extra NFS hop

The original hypothesis was that r9's mergerfs pool (NAS + production's internal disk
re-exported over NFSv4.2, an extra hop that doesn't exist on production) might itself
be implicated. Task 8's repro write from r9 resolved to `/mnt/nas` — the same branch
production uses directly — **not** `/mnt/prod-internal` (the branch that only exists on
r9, via that extra NFS hop). This is evidence *against* the extra-hop theory for this
particular write, though it's one data point: mergerfs picks branches by free space at
write time, not deterministically by host, so it doesn't rule the hop out for other
writes that might land differently.

### Structural limitation: mode-000 files can't get a branch tag

`permissions-monitor.sh` tags every 777 anomaly with its mergerfs branch via
`getfattr --only-values -n user.mergerfs.basepath`. Mode-000 anomalies never get a real
tag — `getfattr` needs read access to read *any* xattr on a file, and mode 000 denies
that even to the file's owner without root, which this monitor family never uses. This
is a **permanent, structural limitation of the current design**, not a bug to fix: the
000-class anomalies (the larger half of the backlog: podcasts and photos especially)
will always show `mergerfs branch: unknown` in the forensic log.

## NAS Log Manager pointer (from Task 3)

`nas-log-snapshot.sh` (called automatically by `permissions-monitor.sh` on every new
detection) pulls what it can over SSH when the NAS is reachable:

- **Readable directly:** `media_serv.log`, `media_serv_worker.log`, `thumb_core.log`,
  `thumb_worker_0.log`, `thumb_worker_background_0.log` — grepped for the detection
  date automatically.
- **Root-only (no sudo available to `alleyneja`):** `filemgr_serv_fileOperations.slog`,
  `filemgr_serv.slog`, `index_serv_inotify.slog`, `index_serv_event.slog`,
  `ctl_serv.slog` / `ctl_serv.slog.1`, `storage_serv.slog`,
  `storage_serv_ugvolume.slog`, `taskmgr_serv.slog`, `jobmgr_serv.slog`. The script only
  reports size/mtime for these — **check their actual content by hand, via the UGOS
  Log Manager app in the NAS web UI**, when following up on a specific detection.
  `filemgr_serv_fileOperations.slog` and `index_serv_inotify.slog` are the most likely
  to show which process touched a file; `ctl_serv.slog` and `storage_serv.slog` cover
  NAS-level job/storage activity.
- **`ctl_serv.slog.1` specifically covers the Sep 20 burst window** (the ~6,700-item
  Plex trash incident) — its mtime is 2026-09-20, i.e. it rolled over right around that
  event and is the log to pull first if that burst ever needs a second look.

## Access cleanup still owed

Per `reference_r9_and_nas_access_notes`: NAS SSH is normally closed, and `alleyneja`
was added to the `adm` and `systemd-journal` groups on Sep 21 specifically to support
this investigation (read-only log access). **Once this investigation is done, ask Jay
to close NAS SSH again and run, on the NAS:**
```
sudo gpasswd -d alleyneja adm
sudo gpasswd -d alleyneja systemd-journal
```
This has been owed since the last time this investigation was opened — don't let this
pass close it out silently. It's an access-hygiene step, separate from whether root
cause was ever found.
