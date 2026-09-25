# Fleet Permissions Monitor

Operational guide for the alert-first permissions monitor built under D12
(private tracker `alleyneja/mediahub-issues` #33, formerly `~/mediahub-cleanup.md`) to catch the recurring mode-000 / mode-777 anomaly pattern
across the whole `/mnt/media` pool, on both production and r9. This monitor **never
auto-fixes** — it detects, logs, and queues. 
**Root cause (found 2026-09-25):** the mode-000 half is a defect in UGREEN's `ugacl`
kernel on the NAS. When the NAS evicts an inode under memory pressure and a client asks
for it again by NFS file handle, the NAS renders the mode wrong (000/700) and denies the
read. A lookup by name renders it correctly. Nothing changes on disk (no ctime change).
r9 made it worse because it's a second full-library client, which means more evictions.
**Fix:** `lookupcache=none` on the `/mnt/nas` mount on both hosts (see
`scripts/nas-lookupcache-fix.sh` and `system/fstab`). Measured in the same eviction round:
normal mount 0/200 readable, `lookupcache=none` 200/200. The monitor stays in place to
confirm the fix holds. The mode-777-on-create half is separate and harmless: NAS
directories are 777 and ugacl ignores umask.

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
| Digest accumulator (routine, below-threshold findings) | `/home/jay/logs/permissions-digest-pending.txt` | same path, separate file |
| Daily digest sender cron | `7 8 * * * /home/jay/mediahub-configs/scripts/permissions-digest-send.sh` | `7 8 * * * /home/jay/scripts/permissions-digest-send.sh` |
| Fleet identity audit cron (repo-wide, production only) | `0 6 1 * * /home/jay/mediahub-configs/scripts/check-fleet-identity.sh` | n/a — audits the repo's `stacks/`, doesn't need a per-host run |

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
   /home/jay/mediahub-configs/scripts/permissions-apply-fix.sh --all             # fixes EVERYTHING currently queued (must be explicit)
   /home/jay/mediahub-configs/scripts/permissions-apply-fix.sh /path/to/one/file  # fixes only the path(s) given, leaves the rest queued
   ```
   **As of the 2026-09-22 final review, a bare invocation with no arguments and no
   `--all` now refuses and exits nonzero** — this used to silently attempt to fix
   everything queued. It also now checks `chmod`'s exit status per file: on
   production, ~84% of the queue is `root:root`-owned and this process runs
   unprivileged with no sudo, so `chmod 644` on those fails with `EPERM`. A failed
   file is left in the pending queue and reported separately as "failed" in both the
   forensics log and the Discord summary — it is **never** silently marked fixed.
   Successfully-fixed files are also cleared from `permissions-monitor-seen.txt` (not
   just the pending queue), so if one of them genuinely flips again later, the
   monitor will re-alert on it instead of treating it as permanently "already seen."

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
corroboration, not proof. **Correction (2026-09-22, final review):** this doc previously
said the 777-on-creation lead "hasn't been tested against the other branch (the local
ext4 disk)" — that overstated the gap. The forensics log already contains 540
mode-777 anomalies tagged `mergerfs branch: /mnt/internal` (the local branch) versus
~3,600+ on `/mnt/nas`, so the corpus already has hundreds of 777s on the local branch
too. What's actually still untested is *controlled creation-time* writes on the local
branch specifically — the 540 are pre-existing files found by the scanner, not a
deliberate repro like the three data points above, so they don't settle whether the
local branch forces 777 on creation the same way `/mnt/nas` does. **This is a concrete,
testable lead for a future session** (run `permissions-repro-test.sh`-style writes
targeted at the local branch specifically), **and it only covers the 777-anomaly
class** — it does not explain how any file ends up at mode 000.

**Open lead (2026-09-22, final review): host-specific mount-config difference?** Jay
has raised, and this has not yet been investigated, whether the 777-on-creation
behavior is specific to *how each host's mergerfs pool is built* rather than a
universal property of a given branch. Production and r9 build their pools differently
(see "D12 hypothesis update" below — r9 adds an extra NFS hop production doesn't have).
A future session should compare the two hosts' exact mount options/branch config
(`mount | grep /mnt/media`, mergerfs `-o` flags, fstab/systemd unit, NFS version per
branch) side by side before assuming either host's repro result generalizes to the
other.

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

## Alert volume control: burst threshold + daily digest (2026-09-22 final review)

Real usage showed ~40 Discord alerts/day, mostly routine 777-on-creation writes — at
that rate the channel was headed for a mute, which would defeat the whole point of an
alert-first design. Jay's decision was explicitly **not** to suppress the 777 class
(he wants to keep visibility into it — it may connect to the still-open
production-vs-r9 mount-config hypothesis above), just to stop paging on every single
occurrence of routine noise.

`permissions-monitor.sh` now has a `BURST_THRESHOLD` (default `50`) at the top of the
file. Detection, forensic logging, and queueing are **unchanged** regardless of
threshold — every anomaly still gets a full forensic block and lands in the pending
queue. Only the *immediate Discord alert* decision changes:

- **`new_files` count >= `BURST_THRESHOLD`:** alert immediately, exactly as before —
  this is the incident-signature case (e.g. the Sep 20 burst, which was in the
  hundreds-to-thousands).
- **Below `BURST_THRESHOLD`:** no immediate alert. Instead, one line
  (`timestamp host=<host> count=<n>`) is appended to
  `/home/jay/logs/permissions-digest-pending.txt`.

`scripts/permissions-digest-send.sh` reads that accumulator once a day (cron'd
`7 8 * * *` on both hosts — see the table above), sends **one** Discord message
summarizing total findings, run count, and the date range covered, then clears the
accumulator. If the accumulator is empty, it sends nothing (no "0 findings" spam). If
the Discord send itself fails, it leaves the accumulator alone (logs a warning to the
forensics log) rather than losing that day's rollup.

## Fleet identity audit (`check-fleet-identity.sh`)

Audits every `stacks/*/docker-compose.yml` for PUID/PGID/`user:` declarations against
the fleet-wide expected identity `1000:1000` (`jay`). This script was written during
D12 scoping (before any monitor code existed) and was previously **undocumented and
not wired into cron** despite being committed — the plan's Task 2 brief said "Task 6
wires this into a monthly cron check," which never happened until the 2026-09-22 final
review.

**What it actually proves, and what it doesn't:** it can only evaluate stacks that
*declare* an identity. As of 2026-09-22, 17 of 31 stacks declare no PUID/PGID/`user:`
at all — including Immich, which has been directly observed writing `root:root` files
into the pool (see the real backlog composition above: photos is the single largest
mode-000 category). The script's output distinguishes "N stacks declare a *conflicting*
identity" (a real problem, nonzero exit) from "M stacks declare *no* identity at all"
(informational only — they run as whatever the image's default is, which may be root,
but the script has no way to prove that one way or the other from the compose file
alone). Treat "no identity declared" as a still-open item, not a clean bill of health.

Also fixed 2026-09-22: the `user:` regex previously only matched the quoted numeric
form (`user: "1000:1000"`) — an unquoted or named value like `user: 0:0` or
`user: root` silently passed as compliant. It now matches any `user:` line with a
value and compares the normalized result.

Cron (production only — this audits the repo, not a per-host runtime state, so one run
covers the whole fleet):
```
0 6 1 * * /home/jay/mediahub-configs/scripts/check-fleet-identity.sh >> /home/jay/logs/check-fleet-identity.log 2>&1
```

## Log rotation

`permissions-forensics.log` had no rotation and was already 5.5MB+/93K+ lines on
production as of the 2026-09-22 final review, growing roughly 300KB/day per host.
Added `/etc/logrotate.d/permissions-monitor` (system logrotate config, not tracked in
this repo — same as every other `/etc/logrotate.d/*` entry on this host) on **both**
production and r9:
```
/home/jay/logs/permissions-*.log {
    weekly
    rotate 6
    compress
    delaycompress
    missingok
    notifempty
    create 644 jay jay
}
```
The glob deliberately only matches `.log` files (currently just
`permissions-forensics.log`) — it does **not** touch `permissions-monitor-seen.txt`,
`permissions-pending-fixes.txt`, or `permissions-digest-pending.txt`, which are state
files, not logs, and must never be rotated/truncated out from under a running queue.

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

## Canary: live proof of the lookupcache fix (2026-09-25)

`scripts/permissions-canary.sh` (production cron `11,41 * * * *`) keeps 200 files in the hidden NAS dir
`/mnt/nas/.permissions-canary`:

- `ctl-*` are checked **only** through `/mnt/permissions-canary-control`. That's a read-only NFS mount deliberately
  set up without `lookupcache=none` (see `system/fstab`), so it flips when the NAS evicts inodes.
- `fix-*` are read **only** through the fixed `/mnt/media`.

The two halves never overlap, because a by-name lookup heals the NAS-side state. `permissions-monitor.sh` prunes
the directory for the same reason.

| Control | Fixed | Meaning | Discord |
|---|---|---|---|
| clean | clean | nothing happened | none |
| flipped | clean | NAS evicted and the fix held (the evidence we want) | info post, then control is healed by name |
| any | denied | **the fix is incomplete** | alert (once per transition) |
| mount missing | - | canary blind | alert |

Log: `~/logs/permissions-canary.log`. This replaces the retired per-minute sentinel pilot, which was confounded
because stat'ing every minute kept its files warm.
