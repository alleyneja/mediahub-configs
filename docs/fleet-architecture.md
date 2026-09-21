# Fleet Architecture

**Status:** DRAFT 2026-09-20, revised after Jay's first review — D1 approved (phased split by role); end state (new machine as production's successor) added; awaiting final read
**Purpose:** record what each machine in the mediahub fleet is *for*, why, and how it got that way,
so the setup can be understood, changed, or rebuilt by someone who was not there.

This document follows the method in [network-architecture.md](network-architecture.md):
**requirements first, then options that satisfy them, then a risk-weighed decision, logged with its
reasoning.** A decision that turns out wrong later is acceptable if the record shows what we knew
when we made it. Amend this file instead of re-deriving the design in conversation.

Three principles run through everything below:

1. **Requirements before tools.** Ask what a thing must do, must affect, and must never touch,
   before choosing software or hardware.
2. **Roles, not machines.** A machine has a permanent, boring name. A *role* (production, staging,
   games) is a label that points at whichever machine currently does that job.
3. **Household services must not depend on the management layer.** If the machine that oversees the
   others goes down, everything already running keeps running.

---

## 1. Requirements

| # | Requirement | Origin |
|---|---|---|
| F1 | Interrupt the household as little as possible when moving services. Every household service (Plex, Nextcloud, Vaultwarden and the rest) should be down for **seconds to a few minutes** per move, planned for a quiet window (not e.g. a Sunday during live sport). | stated 2026-09-20 |
| F2 | The media library (8.8 TB on the production machine's internal drive) must stay intact and have exactly **one** writer at a time. Two app instances with separate databases pointed at the same files is the failure to avoid — see [fresh-machine-bring-up.md](fresh-machine-bring-up.md). | derived from a prior rehearsal |
| F3 | Game streaming and emulation should not depend on the production machine or the NAS being reachable while playing. | derived |
| F4 | A future home-security system (Home Assistant, possibly Frigate) runs on a **different machine** from the GPU/game workloads, so a game crash or restart cannot take security down. | stated 2026-09-18 |
| F5 | Roles are decoupled from hostnames. A machine can be retired or swapped without renaming everything that mentions it. | stated 2026-09-20 |
| F6 | Household services keep running if the management/hub role is down. | derived from principle 3 |
| F7 | A machine can be rebuilt from this repo plus the documented manual steps, by someone other than the original builder. | stated 2026-09-20 |
| F8 | The trust set is unchanged from network-architecture R4: a small closed group of people and devices. | inherited |
| F9 | The new machine is the **successor to production**, bought to run the full service set. The most demanding workload (emulation and game streaming) set its specification; everything else fits within that headroom. A local LLM is a possible future workload. | stated 2026-09-20 |
| F10 | All three servers stay in service. The older hardware is repurposed, not discarded. | stated 2026-09-20 |
| F11 | The gaming PC is part of the fleet but is a **client, not a server**: Windows 11, always attached to a display, and the normal place administration is done from. Nothing in the plan may make a server depend on it being on. | stated 2026-09-20 |

### Non-requirements

Recorded so they are not re-litigated:

- **Automatic failover between machines.** Downtime of minutes during a *planned* change is fine;
  automatic high availability is not being built.
- **Moving the 8.8 TB library now.** See D1 and §4 — it is deliberately left where it is.

---

## 2. Fleet inventory (as of 2026-09-20)

| Machine | Hardware | Address | Role today | Role planned |
|---|---|---|---|---|
| `mediahub-production` | Intel i7-7700, 32 GB DDR4, Quadro P400, 2 TB NVMe (OS/Docker), 12 TB HDD (media) | 192.168.0.21 | Everything: media, apps, Plex, Live TV, game streaming | Storage and library host (it holds the 8.8 TB) and backup target; services move off it in phases (proposed, see Q7) |
| `mediahub-r9` | Ryzen 9 9900X, 64 GB DDR5-6000, RTX 5070 Ti 16 GB, 2 × 2 TB NVMe | 192.168.0.22 (reserved in the router; was `.149` until 2026-09-21) | Bring-up and burn-in | Primary host: successor to production, running the full service set once phases 2–4 are done |
| `mediahub-staging` | Alienware X51 R2, i7-4790, 16 GB, GTX 1650 | 192.168.0.20 | Rehearsal/practice machine | Candidate for the security system (F4) |
| NAS | UGREEN NASync DH4300 Plus | 192.168.0.23 | Network storage, NFS | Unchanged |
| `gaming-pc` (Tailscale name `gaming-desktop`) | Windows 11 workstation; never headless | 192.168.0.206 | Jay's daily machine and the usual origin of SSH sessions to the servers; holds the installed PC games | Unchanged. Part of the fleet as a client/workstation, not a server |

### LAN addresses (fixed pattern, router reservations)

| Address | Host | Notes |
|---|---|---|
| `192.168.0.1` | Router (Arris SBG8300) | Hands out DHCP; DNS list it gives clients: AdGuard `.21`, then `1.1.1.1` |
| `192.168.0.20` | `mediahub-staging` (future `mediahub-x51`) | Being retired |
| `192.168.0.21` | `mediahub-production` (future `mediahub-i7`) | AdGuard DNS for the whole network. Do not change without changing the router's DNS setting first |
| `192.168.0.22` | `mediahub-r9` | Moved here from `.149` on 2026-09-21 |
| `192.168.0.23` | UGREEN NAS | Mounted BY ADDRESS in production's `fstab`. Reservation added 2026-09-21 (had none before) |

The next new machine takes the next free number (`.24`). Gaming PC and phones stay on ordinary DHCP.

### Storage facts (measured 2026-09-20)

| Location | Size | Used | Free | Notes |
|---|---|---|---|---|
| Production internal drive (`/mnt/internal`) | 11 TB | 8.8 TB (85%) | 1.6 TB | TV 5.0 TB, movies 3.2 TB, music 0.4 TB, photos 0.1 TB, emulator library 78 GB |
| NAS (`/mnt/nas`) | 22 TB | 11 TB (48%) | 12 TB | |
| Combined pool (`/mnt/media`) | 33 TB | 20 TB | 13 TB | mergerfs across the two above |

Game data, by where it lives:

| Data | Size | Location | Consumer |
|---|---|---|---|
| Emulator ROMs, BIOS, saves, configs | 78 GB | `/mnt/internal/arcade` (not the pool — mergerfs blocks the file-access mode some emulators need) | ES-DE, emulators, **RomM** (mounts this folder as its library) |
| Switch ROMs | 47 GB | `/mnt/media/games/roms/switch` (pool) | RomM, Switch emulation |
| PC game installers | 918 GB | `/mnt/media/games/pc` (pool) | Archive only. Installed games live on the gaming PC |

Transfer arithmetic at gigabit speed (~118 MB/s measured): the whole 8.8 TB library ≈ **21 hours**;
the 125 GB of emulator + Switch ROMs ≈ **18 minutes**.

---

## 3. Decision D1 — move to the new machine in phases, by role

**Decided:** 2026-09-20, by Jay: the phased split by role (option B). The end state is the new machine as production's successor (F9) with the older machines repurposed (F10). Option C is the long-term direction.

### Options considered

| Option | What it means | Verdict |
|---|---|---|
| A. Replace production wholesale | Move every service to the new machine in one cutover | Rejected as a *method*, not as a destination (it is the same end state as B). The 8.8 TB library is on the old machine (≈21 h to copy), every household service would move at once, and F1/F2 would be violated in one step. |
| **B. Split by role, phased** | GPU jobs move first, then the remaining services one stack at a time; the old machine keeps the library until it moves; a third machine takes security | **Chosen.** Nothing existing is touched until each piece is proven. |
| C. Move the library to the NAS first, make every machine compute-only | Cleanest end state | Deferred, not rejected: the direction Jay is working toward. The NAS has room (12 TB free vs 8.8 TB) but would end ~88% full, the copy takes ~21 h, and it is not required for anything today. Revisit if the internal drive passes ~90% or production is retired. |
| D. Physically move the 12 TB drive into the new machine | Avoids the copy | Not evaluated: case fit and cabling unverified, and it concentrates the library and the game box in one machine, against F4/F6. |

### Reasoning

- The new machine was bought as production's successor (F9); emulation merely set the specification
  because it is the most demanding workload. Moving the GPU jobs first is **sequencing**, not the
  machine's final role: that work needs only ~125 GB of ROMs, so it moves with a short copy and **no**
  interaction with the media library, satisfying F1, F2 and F3 immediately.
- Plex is the service viewers notice most (F1). It moves after a rehearsal, in a planned window.
- Security on its own machine (F4) is independent of all of this and can happen whenever.

### Risks accepted

| Risk | Mitigation |
|---|---|
| Production stays a single point of failure for the library and household services in the interim | Unchanged from today; no new exposure introduced by D1 |
| 1 Gb link between machines limits shared-storage throughput | Measured fine for video streaming (network-architecture §5.1); ROMs are copied locally instead of streamed |
| Two databases against one library if a stack is started carelessly on the new machine | F2; media-facing stacks stay stopped/unstarted on the new machine until a planned cutover |
| Plex identity/claim conflicts if two servers run at once | Rehearse before cutover; only one Plex instance is ever live |

### Revisit triggers

Internal drive above 90%; production hardware failure; a household-service outage tolerance
different from F1; or the security system's requirements changing.

---

## 4. Phasing

Each phase has a gate: do not start the next until the gate passes.

| Phase | Work | Gate |
|---|---|---|
| 0 | New machine (`mediahub-r9`) bring-up and burn-in | CPU/RAM soak clean; GPU soak clean; temperatures sane |
| 1 | Groundwork: reserve the new machine's IP in the router; add it to the NAS export allowlist; install Docker and Tailscale; firewall rules | Machine reachable and mounts what it needs read-only |
| 2 | **CLOSED 2026-09-21.** Game streaming moves: r9 is master for ROMs, saves and states; bring up streaming on the new GPU | A real game plays end to end. **Passed:** confirmed on multiple consoles/emulators (God of War Collection 60 FPS; MW2 is an outlier at ~18 FPS, CPU-bound on RPCS3's emulated GPU thread, rated only "Ingame" upstream) |
| 3 | Plex/transcoding moves: rehearse against a copy, then a planned short cutover in a quiet window | Rehearsal passes; viewers unaffected off-window |
| 4 | Remaining household services (Nextcloud, Vaultwarden, the *arr apps and the rest) move one stack at a time, each within F1's seconds-to-minutes | Each stack verified before the next |
| 5 | Old production machine repurposed (storage/backup role, proposed) | Library safely served or moved (option C) first |
| 6 | Security system on its own machine (F4) | Independent of phases 1–5; can happen at any time |
| 7 | Naming pass (see §5) | Tracked and committed as one change |

The hub-and-satellite idea (one machine overseeing the others) is a later layer on top of this and
must respect F6. It is tracked in the private backlog until designed.

---

## 5. Naming and roles

- **Decision D2 (2026-09-20, Jay): hostnames are neutral and named for the hardware.** A hardware
  name never changes meaning when a machine's job changes: `mediahub-r9` (Ryzen 9 9900X) is always
  that box, whether it is production, later demoted to staging, or scrapped.
  - `mediahub-r9` — the new machine. Renamed from `mediahub-arcade` on 2026-09-20 (before anything
    depended on it).
  - `mediahub-i7` — the i7-7700 machine, **currently still named `mediahub-production`**; renamed in
    the cutover naming pass (phase 7).
  - `mediahub-x51` — the Alienware X51 R2, **currently still named `mediahub-staging`**; same pass.
  - `mediahub-production` and `mediahub-staging` then become **role names** (DNS aliases) that point
    at whichever machine holds the role.
  - The gaming PC keeps its own name; it is a client, not a server.
- **Hostnames are permanent and unique.** Never reuse an old machine's name for different hardware:
  every document, log and note that says the old name would silently change meaning.
- **Roles are DNS aliases** (for example `prod.lan`) pointing at the machine that currently holds the
  role. Moving a role means moving the alias, not renaming machines.
- **What actually couples to a machine today:** its IP address (192.168.0.21 appears in about 18
  files, including the Caddyfile, Sunshine config and Homepage), far more than its hostname (about
  10 files, mostly documentation). At cutover, the new holder of a role takes the role's address and
  the outgoing machine gets a new one.
- Tailscale registers each machine as a separate device; renames must update those entries too.
- Renames happen as **one tracked pass** after migration, committed together.

---

## 6. Bringing up `mediahub-r9` (installed as `mediahub-arcade`) — what happened 2026-09-20

Background for anyone repeating this. General bring-up lessons are in
[fresh-machine-bring-up.md](fresh-machine-bring-up.md); this is specific to the new hardware.

1. **BIOS:** confirm all RAM and both NVMe drives are detected, enable the memory profile (EXPO).
   Verified afterward from the OS: rated 6000 MT/s, configured 6000 MT/s, matching part number.
2. **Install media:** Ubuntu 24.04.4 desktop image, written as files to a USB stick. It boots under
   UEFI and ships a 6.17 kernel, new enough for the X870 board and RTX 5070 Ti. Verify the image
   against its own checksum list before booting from it.
3. **Install:** erase-disk install onto one NVMe, no disk encryption (unattended reboots), install
   third-party graphics drivers. The two NVMe drives are identical; **their `nvme0`/`nvme1` names can
   swap between boots, so identify drives by serial number or `/dev/disk/by-id`, never by number.**
   The two drives also sit in slots of different width (one Gen4 x4, one Gen4 x2); the x2 drive holds
   the OS, which is the less bandwidth-sensitive role.
4. **Remote access:** install the SSH server, then install the controlling machine's public key with
   `ssh-copy-id` from a **real terminal** (it needs a keyboard; it fails silently from a tool prompt
   without one). Passwordless sudo was granted for the setup and burn-in period only.
   **Hardening (2026-09-20), matched to `mediahub-production`:** an sshd drop-in
   (`/etc/ssh/sshd_config.d/hardening.conf`) with root login off, password authentication off,
   `MaxAuthTries 3` and X11 forwarding off; a fail2ban `sshd` jail (5 failures in 10 minutes bans for
   1 hour, nftables action); auditd running with stock rules; UFW default-deny with the LAN, the
   Tailscale range and Docker bridges allowed; unattended-upgrades on stock settings. Host-specific
   rules on production (Plex port, moved Tailscale port) were deliberately not copied. Verified with a
   fresh key-only login, and a password attempt is refused. Passwordless sudo remains until migration
   is finished.
5. **Memory accounting:** 64 GB installed reads as ~60.5 GiB usable. This is normal: 2 GiB reserved
   for the CPU's integrated graphics, small firmware holes, and ~1 GiB of kernel page bookkeeping.
6. **Burn-in** (see the gpu-burn note below): CPU/RAM `stress-ng --cpu 24 --vm 4 --vm-bytes 80%
   --vm-method all --verify --timeout 30m` — passed, 0 failures, CPU peak 57 °C, no throttling and no
   hardware errors in the kernel log.
7. **GPU burn-in on Blackwell (RTX 50-series):** the distribution's CUDA toolkit (12.0) predates the
   architecture and cannot compile for it directly. `gpu-burn` builds and runs correctly with
   `make COMPUTE=90` (portable code the driver translates at run time). It **must be run from its own
   directory** — launched from elsewhere it cannot find `compare.fatbin` and dies immediately with
   "couldn't find compare kernel". Result on this machine: 30 minutes, 0 errors, GPU peak 73 °C, holding
   its 300 W power cap (the only throttle flag seen, which is normal at full load), no GPU faults in the
   kernel log.
8. **NAS export for the new machine (2026-09-20):** the NAS's UGOS web UI has no page for per-host NFS
   rules, so the address is allowed by appending a read-only entry to `/etc/exports` on the NAS and
   running `exportfs -r` (reloads without restarting NFS, so existing mounts are unaffected):
   `192.168.0.149(ro,sync,insecure,no_wdelay,no_root_squash,anonuid=65534,anongid=65534,sec=sys)`.
   **Stale since 2026-09-21:** r9 is now `192.168.0.22`. The NAS export now lists `.22` (ro, added 2026-09-21; backup `/etc/exports.bak.pre-r9-ip22`). Test mount from r9 verified (NFSv3 like production; NFSv4 is not offered). Stale `.149` entry removed (backup `/etc/exports.bak.pre-rm-149`). Flip `.22` to `rw` at cutover.
   The NAS regenerates its NFS config from an internal database when the NFS service restarts, so this
   entry may be lost after a NAS reboot or update; if the new machine suddenly fails to mount, re-add it.
   Verified: the share mounts and lists, and writes are refused. Also install `nfs-common` on the client.

---

## 7. Open questions

| # | Question | Notes |
|---|---|---|
| Q1 | How much downtime can Nextcloud and Vaultwarden tolerate during a cutover? | **Answered 2026-09-20:** seconds to a few minutes. Now part of F1. |
| Q2 | Which machine plays PC games, and how? | The 918 GB in `/mnt/media/games/pc` is an installer archive, not an installed library; installed games live on the gaming PC. Streaming Windows games from a Linux host is a separate question (compatibility layer) and is not assumed here. |
| Q3 | ROM master copy and save direction? | **Answered 2026-09-21:** r9 is master for ROMs, saves and states (it is where games are played, and its library is the newer one: extracted playable PS3 copies). No automatic ROM sync: production keeps the original archives and its own Switch library (`/mnt/media/games/roms/switch`, identical to r9's). Saves flow one way, r9 to production, nightly 04:30 by `scripts/backup-r9-saves.sh` (production cron): additive only, overwritten files kept under `_versions/<date>/`, destination `/mnt/media/arcade/backups/r9-saves/`. Nothing ever flows back into r9. RomM is no longer a constraint: idle since Aug 23 (two users, 25 ROMs, 0 saves); retiring it is a separate decision. |
| Q4 | Router: reserve the new machine's address. | **Answered 2026-09-20:** done, `192.168.0.149` reserved, and added to the NAS export allowlist (read-only; see section 6, step 9). Note: the connection to the machine drops briefly whenever the router's static-address list is saved. |
| Q5 | What tooling makes the "hub" layer? | Candidates: existing monitoring plus configuration-management or a container-management agent. Must satisfy F6. |
| Q6 | Where do decisions that name weak spots live? | Not in this public repo. A separate private repository, for things worth keeping in several places but not public, is planned (tracked in the private backlog). |
| Q7 | What should the older production machine do once services move off it? | Proposed: storage/library host and backup target, since it holds the 8.8 TB drive. Needs Jay's confirmation. |
| Q8 | Rename `mediahub-arcade` to a neutral name now? | **Answered 2026-09-20:** yes, done. Now `mediahub-r9`; see D2 in section 5. |

---

## 8. Change log

| Date | Change |
|---|---|
| 2026-09-20 | Initial draft: requirements F1–F8, fleet inventory, decision D1 (recommended), phasing, naming, arcade bring-up record. |
| 2026-09-20 | Added the gaming PC to the inventory and F11 (client/workstation, not a server). Added D2 (neutral hardware-based hostnames); the new machine renamed `mediahub-arcade` to `mediahub-r9`. |
| 2026-09-20 | Revised after Jay's review: D1 approved; added F9 (new machine is production's successor) and F10 (reuse all hardware); F1 tightened to seconds-to-minutes; PC games folder corrected to an installer archive; phasing extended to the full service move; open questions Q7, Q8 added. |
| 2026-09-20 | Phase 1 groundwork: router IP reservation done (Q4), Tailscale joined, SSH/fail2ban/auditd hardened to match production. |
| 2026-09-20 | NAS export allowlist: r9 added read-only (Q4 closed). Documented that UGOS has no UI for per-host NFS rules. |
| 2026-09-21 | Moved `mediahub-r9` from `192.168.0.149` to `192.168.0.22` (edited the existing router reservation for MAC `30:56:0f:b6:7e:18`). Verified: r9 answers at `.22` with the same SSH host key, correct gateway, DHCP lease from the router, Sunshine listening, Tailscale unaffected; NAS mount and AdGuard on production unaffected. Sunshine `csrf_allowed_origins` updated. NAS `.23` reservation added the same day; all four hosts (staging, production, r9, NAS) now appear in the router's static devices. NAS `/etc/exports` now allows `.22` (ro) and r9 test-mounted it. Stale `.149` removed. IP cleanup complete. |
| 2026-09-21 | Phase 2 closed. r9 declared master for ROMs, saves and states (Q3). Nightly one-way saves backup r9 to production added (`scripts/backup-r9-saves.sh`, cron 04:30, tested: 5/5 sources ok). Switch library needs no copy: production already holds an identical one at `/mnt/media/games/roms/switch`. |
