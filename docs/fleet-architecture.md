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
| 3 | Plex/transcoding moves: rehearse against a copy, then a planned short cutover in a quiet window | Rehearsal passes; viewers unaffected off-window. **Rehearsal PASSED 2026-09-21** (see 6b). **CUTOVER DONE 2026-09-21, COMPLETE** (see 6c) |
| 4 | **Amended by D3:** only the compute-role services move to r9 (Immich, Jellyfin, Minecraft, Stirling PDF and similar); the essentials and the *arr pipeline stay on production. One stack at a time, each within F1's seconds-to-minutes | Each stack verified before the next |
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

## 5b. Decision D3: what each machine is for (2026-09-21, Jay)

**Decided:** each machine does what it is uniquely good at, and services sit where their failure hurts least. Amends F9: r9 is the *compute* host, not a host for the full service set.

| Machine | Role | Why | Notes |
|---|---|---|---|
| `mediahub-r9` | **Compute and experiments:** Plex and transcoding (done), game streaming/emulation, Immich (+ML), Jellyfin, Minecraft/Pterodactyl, future local LLM, other heavy or non-critical apps | Only machine with real CPU/RAM/GPU headroom; the most likely to reboot or crash (games, drivers) | Expected to be on most of the time |
| `mediahub-production` (i7) | **Core and storage:** the "always-on essentials" (AdGuard DNS, Caddy, Vaultwarden, Authentik, Nextcloud), the library disk, the *arr and download pipeline (must share a device with the library), Threadfin | Stable, always on, no gaming, holds the 8.8 TB | Its memory pressure (see below) is relieved by moving heavy apps to r9 |
| NAS | Bulk storage and backup target; also the store for security footage | 22 TB, always on | Unchanged otherwise |
| `mediahub-staging` (x51) | **Security system** (Home Assistant, cameras); footage stored on the NAS | Separate failure domain from games (F4) | A second AdGuard here was considered, not decided (see Q9) |
| gaming PC | Client only | F11 | Unchanged |

**Requirements captured for Phase 4 (2026-09-21):**
- Data loss: last night's backup is acceptable for moves (a fresh backup is still taken immediately before each move, since it is cheap).
- Never down, even briefly: **AdGuard DNS** only (Jay's most-used service). Everything else tolerates F1's minutes; nobody depends on these services completely yet.
- Pace: not fixed; follows the role split (a short list, not the full service set).
- Q7 answered: production stays as the core and storage host, not a machine to be emptied.

**Measured, production memory (2026-09-21):** swap 3.7 of 4.0 GB used, but only 10 of 31 GB of RAM in use with 20 GB available: the kernel has parked idle services in swap to keep file cache (swappiness 60). Not a shortage, but idle essentials (Authentik 376 MB, Stirling PDF 478 MB) are swapped out and slow on first use. Largest resident users: Minecraft server 1.1 GB, Immich, Jellyfin, `rreading-glasses-db`, AdGuard.

**Revisit if:** r9 proves stable enough to host essentials; or production's disk/CPU becomes the bottleneck; or the security machine is not the x51.

---

## 5c. Decision D4 (PROPOSED, not yet in effect): per-machine update policy (2026-09-21)

**Status: proposed by Claude from a survey; Jay liked the idea, has not approved the detail.** Nothing below is implemented except what is marked "today".

**Today:** production's root crontab runs `apt update && apt upgrade -y && reboot` on **Sunday and Wednesday at 03:00**, and `unattended-upgrades` is enabled. r9 has `unattended-upgrades` enabled with no automatic reboot and no held packages. Container images are never auto-updated (standing rule; WUD only reports). Production also restarts Wings daily at 03:00 to pick up its rolling `pterodactyl.lan` certificate.

**Why one policy does not fit every machine:**
- The blanket `apt upgrade -y` + reboot upgrades everything, including Docker and the NVIDIA stack. On r9 a surprise driver or kernel change can silently break Sunshine/NvFBC (two known failure modes; see the arcade notes).
- **Reboots are now coupled across machines:** r9 mounts production's disk and Plex's Live TV depends on Threadfin on production, so every production reboot takes those offline for a couple of minutes. Production and r9 must never reboot in the same window, and r9 must come up after production.
- Container updates are a separate topic from OS patching and stay deliberate and per-service.

**Proposed policy:**

| Machine | OS patching | Reboot | Notes |
|---|---|---|---|
| production | keep Sun+Wed 03:00 | automatic (as today) | add a hold on NVIDIA and Docker packages so those change on purpose |
| r9 | unattended security updates only | **manual**, always after production | hold NVIDIA driver and kernel meta-packages |
| NAS | UGOS manages itself | manual | quarterly check |
| x51 | same as production once it is a live server | automatic | after it is set up |

**Also proposed:** a Discord alert when any machine has needed a reboot (`/var/run/reboot-required`) for more than a few days. Revisit if r9 becomes an essentials host or if a patch breaks something.

---

## 5d. How we record decisions and changes (adopted 2026-09-21, after Jay's correction)

Every change to a shared service or its configuration gets a record **at the time of the change**, in this document, with: **what** changed (old value to new value); **why** (the evidence, with numbers); **what it gains**; **what it costs or sacrifices**; **what we did not verify**; **how to roll it back**; and **when to revisit**. If the cost side is empty, that is a signal we have not looked hard enough. Recommendations from Claude must state the trade-off *before* asking Jay to make the change, not after. D3, D4 and D5 follow this format.

---

## 5e. Decision D5: AdGuard rate limit raised from 20 to 500 queries per second (2026-09-21, Jay applied it live in the AdGuard UI)

**What:** `ratelimit` in AdGuard Home, 20 to 500. `ratelimit_subnet_len_ipv4` is unchanged at 24. Live change, no restart. Mirrored by hand into `adguard/AdGuardHome.yaml` (the sanitized repo copy; never copy the live file).

**Why (evidence):**
- Before: a controlled burst of 100 distinct queries from r9 got **20 answers and 80 silent drops**. After: **100 of 100 answered**.
- The limit counts per client **/24 subnet**, so every device on `192.168.0.0/24` shares one bucket of 20 queries per second. Tailnet clients (`100.x`) mostly land in separate buckets.
- r9's resolver logged 7 "degraded feature set" events for `192.168.0.21` in a day (including at boot), after which it drifted to `1.1.1.1` and lost every `.lan` name.
- Real demand is above the old cap: in AdGuard's own query log (2.73 million queries, 2026-08-29 to 2026-09-21, about 110-130k a day) **7,723 seconds were at or above 20 q/s** and the busiest second was 261 q/s. The log understates this because dropped queries are never logged.
- The heaviest client is Jay's gaming PC (781k queries): Teams telemetry (`teams.events.data.microsoft.com`, 287k) and a Windows proxy lookup (`wpad.hsd1.fl.comcast.net`, 95k). These are client retry loops against blocked or unresolvable names, not attacks.

**What it gains:** no silent drops for anyone on the LAN (all of them shared the 20 q/s bucket); removes the trigger that made r9's resolver flap; boot-time bursts from any machine no longer starve the others.

**What it costs / sacrifices:**
1. **The safety valve.** The limit stopped a runaway or compromised device from flooding AdGuard. At 500 per /24 bucket that protection is much weaker (25 times the old ceiling).
2. **More load on production's AdGuard** (i7-7700, memory already tight; see D3) and faster log growth. The query log file is already about **1 GB**; nothing here caps it.
3. **It hides, not fixes, noisy clients.** The PC's retry loops are now fully served instead of dropped. The real fix is at the source (why the telemetry lookup is retried 287k times against a blocked name).
4. **Amplification abuse protection** matters only if AdGuard is reachable from the internet.

**Verified by Jay (2026-09-21):** the router's port-forward list has no rule for port 53, so AdGuard is not exposed to the internet through the router; combined with production's UFW (LAN and Tailscale ranges only) that removes cost 4 in practice. This was read from the router UI, not tested from outside. **Still not verified:** AdGuard's CPU use before versus after; whether 20 was ever a deliberate choice (it is AdGuard's default).

**Options considered:** (A) leave 20 and rely on the r9 self-heal timer only: the drops keep hurting every other device; (B) **500 per /24 bucket: chosen**, live, reversible, no restart; (C) disable the limit (0): removes the safety valve entirely; (D) **per-device limit**: `ratelimit_subnet_len_ipv4: 32` with about 100 q/s each, so one noisy device cannot starve the others and the valve stays meaningful. D is the better design but is a config-file change that needs an AdGuard restart (a DNS blip on the never-down service), so it waits for a moment when AdGuard restarts anyway or a second AdGuard exists (Q9).

**Rollback:** AdGuard UI, Settings, DNS settings, Rate limit, back to 20 (and the repo mirror line 33). Expect the drops and r9's drift to return.

**Revisit if:** AdGuard CPU or memory climbs; the query log growth becomes a problem (set a retention limit); the router is found to forward port 53; a second AdGuard is built (then adopt option D); or a device is seen flooding.

**Related finding, deferred to the private backlog by Jay (2026-09-21, primary objectives first):** the query log is about 1 GB with no retention cap, and the gaming PC generates 3 to 4 times the volume of any other client. Also backlogged: the per-device limit (option D).

---

## 5f. Decision D6 (PROPOSED, not yet adopted): a Caddy "front door" on each machine (2026-09-21)

**Status (2026-09-21): approved by Jay; being built in stages. Stage 1-3 done (below); device tests pending; nothing live depends on it yet.** Nothing on r9 or production was changed by the experiment (scratch Caddy on unused ports, memory-backed storage, fully deleted afterwards; checked: no container and no copy of our keys left on r9).

**Why:** today one Caddy on production fronts every `.lan` name (all 37 AdGuard rewrites answer `100.104.43.6`, production's tailnet address). For services on r9 that means (1) a cross-machine hop for every byte, so Immich and Jellyfin traffic would cross the single gigabit link twice and use production's CPU; (2) a published, unauthenticated port on r9 for each service; (3) another hardcoded IP per service. A Caddy on r9, reached by name via AdGuard, removes all three and is the same recipe for every later compute-role stack. It is platform work for batch 2 (Stirling PDF is only the pilot).

**Experiment results (what is now known, not guessed):**
1. With the default layout, Caddy on r9 given the root *certificate* and the intermediate key pair, but **no root key, refuses to start** ("loading root key ... no such file").
2. With Caddy's explicit `pki { ca local { root { cert } intermediate { cert key } } }` config it **starts, issues a certificate, and that certificate chains to the existing root** (`Verify return code: 0` using only production's `root.crt`; leaf issuer key ID equals production's intermediate; leaf lifetime 12 h like production). It generated no CA of its own. So devices would trust r9's Caddy with no change.
3. **Catch:** production's intermediate is valid only **7 days** (2026-09-18 to 2026-09-25) and production's Caddy re-issues it automatically using the root key. A copy on r9 is never rotated by r9's Caddy and would expire, so "copy the intermediate" would be a weekly chore with the key crossing the network each time.

**Proposed design:** give r9 its **own** intermediate: key generated **on r9** (never transmitted), signed once on production with the root key (the root key never leaves production), **long-lived (about 1 year)** and **name-constrained to `.lan`** so a forged certificate for any other name (a bank, say) would be rejected by clients that enforce constraints.

**Gains:** no cross-host hop; no published ports; no IP coupling; root key stays only on production; a compromise of r9 exposes only r9's constrained intermediate.

**Costs / sacrifices:**
1. A second Caddy and a second Caddyfile to keep in step (split the repo's single file per machine); each new service also needs its AdGuard rewrite pointed at the right machine (`100.104.43.6` for production, `100.121.244.45` for r9).
2. **Manual intermediate renewal** (about yearly) on r9; if forgotten, r9's `.lan` sites fail certificate checks.
3. If r9's intermediate key were stolen, forged `.lan` certificates would be trusted until it expires; private CAs are not revocation-checked by clients. Name constraints and a moderate lifetime limit the damage but do not remove it. Impact is limited by F8 (closed trust group).
4. r9's Caddy becomes the front door for r9's names; it is a compute-role dependency.

**Not verified:** that your specific devices (iPhone, Android, Windows, the laptop, the TV) accept a `.lan`-name-constrained intermediate: this must be tested on at least one iPhone and the Windows PC before relying on it; the exact signing procedure (openssl extensions: CA:TRUE, pathlen 0, keyUsage, name constraint); whether Caddy on r9 behaves cleanly at intermediate expiry.

**Build log (2026-09-21):**
1. Key generated **on r9**: `/srv/docker/caddy-r9/pki/intermediate.key` (EC P-256, mode 600, root-owned; never copied elsewhere, not in the repo). Only the certificate request travelled to production.
2. Signed on production with the root key (used in place, no copy left behind): subject `Caddy Local Authority - r9 ECC Intermediate`, CA:TRUE pathlen 0, key usage certSign+cRLSign, **critical name constraints: DNS `lan`, IP `192.168.0.0/16` and `100.64.0.0/10`**, valid **2026-09-21 to 2027-09-21 (renew by 2027-09-01)**. `openssl verify` against production's root: OK.
3. Test Caddy running on r9 from `stacks/caddy-r9/` (`/srv/docker/caddy-r9/`), listening only on r9's tailnet address `100.121.244.45:18443`, serving `r9test.lan` and the deliberately out-of-scope `constraint-test.example.com`. Verified from production with OpenSSL and curl: `r9test.lan` validates through leaf, r9 intermediate, existing root (return code 0, body served); `constraint-test.example.com` is rejected (`permitted subtree violation`, curl exit 60, no content).
4. **Pending:** Jay adds an AdGuard rewrite `r9test.lan` to `100.121.244.45` (live, UI) and tests `https://r9test.lan:18443/` on an iPhone and the Windows PC; then remove the rewrite. Name-constraint enforcement is proven for OpenSSL/curl only; enforcement on iOS, Windows and Android is expected but not verified.
5. After device tests pass: move the front door to 443 (bound to the tailnet address), pilot with Stirling PDF, and split the repo's Caddyfile per machine.

**Fallback if the test fails or the cost is too high:** keep the single Caddy on production and give Stirling a Tailscale-bound port on r9 (compose `100.121.244.45:8085:8080`).

**Rollback:** stop r9's Caddy and point the moved names' AdGuard rewrites back at production; nothing on production changes.

**Revisit if:** a client rejects the constrained intermediate; r9's intermediate expiry causes an outage; or the fleet grows to the point where a small internal CA tool is warranted.

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

## 6b. Phase 3 rehearsal: Plex on `mediahub-r9` (2026-09-21)

Rehearsal server `plex-rehearsal` ran on r9 from `stacks/plex-rehearsal/docker-compose.yml`, with a copy of production's Plex config (17 GB, live copy; cache/logs/codecs excluded) and a **stripped identity** (Plex token, machine IDs, certificate and port-mapping keys removed) so it could not register as, or redirect clients away from, the real server. It was claimed manually as a separate, temporary server named `Plex-r9-rehearsal`.

**What r9 needed (all done; persistence noted):**
- Production exports `/mnt/internal` read-only to `192.168.0.22` only, NFSv4 only (`nfs-kernel-server` installed; `/etc/nfs.conf.d/v4only.conf`; `/etc/exports`). UFW already allowed the LAN subnet.
- r9 mounts production's disk (NFSv4.2) and the NAS (NFSv3; the NAS offers no v4) and unions them read-only with mergerfs 2.33.5 at `/mnt/media`, same layout as production. Movie/TV/music listings matched production exactly (0 differences). **Made persistent 2026-09-21** in r9's `/etc/fstab` (backup `/etc/fstab.bak.pre-plex-phase3`), client options read-write like production's; verified with `mount -a` and by a real reboot (2026-09-21 04:03: all three mounts, Plex and Sunshine came back on their own; boot-time EPG healthcheck ran clean). Writes are still refused because both server-side exports for `.22` remain `ro` until cutover.
- r9 has NVIDIA container toolkit 1.20.1 (same as production) from NVIDIA's apt repo and the `nvidia` Docker runtime.
- Read throughput measured from r9: production's disk 104 MB/s, NAS 50 MB/s.

**Results:**
- Libraries (Movies, TV, Music) loaded with correct `/data/media` paths.
- Forced transcodes ran as full GPU pipelines (`nvdec` decode, `nvenc` encode). HDR10 to SDR tone mapping also ran on the GPU (`tonemap_cuda`).
- Heaviest test: Ghostbusters (1984), 10-bit HEVC HDR10 at 32.7 Mbps, transcoded to 720p 2 Mbps: transcoder CPU 0.5-10% of a core, decoder about 17-36% in short bursts, encoder about 1-5%.
- Two concurrent HDR10 transcodes: both full GPU, about 2-3% CPU each, GPU decoder about 1%. Headroom is large but the limit was not measured.
- Control: with video copied (not transcoded) no transcoder appeared on the GPU.
- The library has **no 4K movies** (778 of 792 are 1080p), so the practical heavy case is 10-bit HDR10 1080p HEVC (561 titles).

**Notes for the cutover:**
- Live TV and Threadfin stay on production for now; the r9 Plex points `threadfin` at `192.168.0.21`.
- **Permissions parity (Jay, 2026-09-21):** production's Plex has read-write access to the library (`/mnt/media` mounted rw, NAS export for `.21` is rw). r9's Plex gets the same, no more, no less: at cutover flip the `.22` exports (NAS `/etc/exports` and production `/etc/exports`) from `ro` to `rw` and `exportfs -ra`. Until then r9 is read-only by the exports alone.
- r9 firewall: `32400/tcp` allowed (mirrors production's rule). The router's port-forward for 32400 must be moved from `.21` to `.22` at cutover (Jay does this in the router UI).
- Real r9 compose: `stacks/plex-r9/docker-compose.yml`. Never run it alongside production's Plex (one shared identity).
- NAS SSH (port 22) was found closed at 03:05 although it worked earlier that night (NFS still fine); it must be reopened to flip the NAS export.
- A fresh, fully-stopped copy of the config is needed at cutover (the rehearsal copy was taken from a running server).
- A stripped-identity server runs heavy background analysis (credits detection) that reads the library over NFS; the real cutover keeps the real identity.
- The rehearsal container is stopped, not removed; its config stays in `/srv/docker/plex-rehearsal` on r9. Remove the `Plex-r9-rehearsal` server from the Plex account when done.

## 6c. Phase 3 cutover: Plex moved to `mediahub-r9` (2026-09-21)

Production Plex stopped 03:19:45; r9 Plex started 03:26:35 (about 7 minutes of downtime; about 11 minutes until the Caddy switch). Only Jay's own paused Plexamp session was active beforehand and no recordings were scheduled.

**What was done, in order:** exports for `.22` flipped to `rw` on both production and the NAS (permissions parity with what production's Plex had); production Plex stopped (`docker stop`, container and `/srv/docker/plex` left intact as the rollback); consistent 17 GB config copy to r9 `/srv/docker/plex`; r9 Preferences edited (P400 `HardwareDevicePath` removed, `customConnections` changed from production's Tailscale IP `100.104.43.6` to r9's `100.121.244.45`); Plex started from `stacks/plex-r9/docker-compose.yml`; Caddy `plex.lan` upstream changed to `192.168.0.22:32400` (edited in place to keep the bind-mount inode; repo mirror `caddy/Caddyfile` updated; live backup `/srv/docker/caddy/Caddyfile.bak.pre-plex-r9`); router port-forward for 32400 moved `.21` to `.22` by Jay.

**Verified:** same machine ID (`e1c6bab8...`), claimed, three libraries, DVR pointing at `threadfin:34400` (reachable from r9); `plex.lan` via Caddy reaches r9; tailnet address answers; Plex Settings shows "Fully accessible outside your network" (Jay); a forced transcode on the real server ran as a full GPU pipeline (nvdec/nvenc).

**Rollback (valid while production's config is kept):** stop r9 Plex (`docker stop plex`), `docker start plex` on production, restore the Caddy line and the router forward. Watch history made on r9 after cutover would not carry back. Production's container restart policy is `unless-stopped`, so its explicit stop survives reboots; do not start it while r9's is running (one shared identity).

**Live TV broke at first, then fixed (03:41-03:50):** tuning failed with "Could not tune channel" (Plex log: `Recorder: Error 16`, ffmpeg `sample rate not set`). Cause: the `Codecs/` folder was excluded from the config copy, and Plex only auto-downloads codecs for on-demand playback, not for Live TV, so r9 had no AAC/AC3/DCA/MP2/MPEG2/VC1 decoders and could not read the streams' audio parameters. Fix: download Plex's own libraries for the running build (`https://downloads.plex.tv/codecs/<build-hash>/linux-x86_64-standard/<lib>.so`) into `Codecs/<build-hash>-linux-x86_64/`. **Rule for any future config copy: include `Codecs/` or pre-seed it.** Test tip: when running Plex's transcoder by hand via `docker exec`, set `FFMPEG_EXTERNAL_LIBS` as Plex does, or it loads no external decoders and every test fails misleadingly.
- Some Live TV lag was reported afterwards (tune time 1.8-6 s). Not root-caused: the maintenance window (default 2-5 AM) was running analysis jobs and Live TV was transcoding on CPU. Re-test after 5 AM before tuning. r9 Plex still has production's `cpus: "4"` cap; raising it to 8 did not change the picture and was reverted.

**Done since cutover:** healthcheck moved to r9 (`/srv/scripts/plex-epg-healthcheck.sh`, root crontab `@reboot`); production's cron entry is commented out (re-enable only on rollback). Rehearsal container and `/srv/docker/plex-rehearsal` removed from r9.

**Follow-ups:**
- Keep production's `/srv/docker/plex` untouched for at least a week as the rollback copy.
- Live TV and Threadfin still run on production; production is now a dependency of r9 (its disk is exported to r9, and Threadfin runs there).

---

## 6d. Phase 4, batch 1: Minecraft moves to r9 (started 2026-09-21)

Decision D3: game servers are compute-role services and belong on r9. Three servers run under one Pterodactyl panel on production (`test-paper` Java/Paper 25500, `bedrock-tailscale` 25501, `bedrock-public` 25502; about 900 MB total). Friends connect over the public internet through the router's port-forward, so the public address does not change; only the forward's target moves to r9.

**Method:** the panel stays on production; r9 is added as a second Wings node and each server is moved with Pterodactyl's server transfer (stop, copy, start). No recreation needed.

**Prep done (no player impact):**
- Wings v1.12.1 (same as production) installed on r9 as `wings.service`; it created the `pterodactyl` user with UID 997 / GID 983, identical to production.
- Node 2 `mediahub-r9` added to the panel (backup of the panel DB taken first: `~/panel-db-pre-r9-node-20260921-0446.sql` on production, mode 600). Memory cap 16 GB, disk 100 GB, allocations `0.0.0.0` ports 25500-25509 like node 1.
- Wings API on r9 uses a Tailscale (Let's Encrypt) certificate for `mediahub-r9.tail3b4ccf.ts.net`, valid to 2026-12-20, renewed monthly by r9's root cron (`0 4 1 * *`; restarting Wings does not stop game servers).
- r9 trusts Caddy's local CA root (`/usr/local/share/ca-certificates/caddy-local-root.crt`) and has `192.168.0.21 pterodactyl.lan` pinned in `/etc/hosts`; production has `192.168.0.22 mediahub-r9.tail3b4ccf.ts.net` pinned in `/etc/hosts` (its resolver does not use MagicDNS). Verified with the panel's own call: node 2 reports Wings v1.12.1, 24 CPUs.

**Found on the way:** r9's systemd-resolved lists AdGuard first but was sticking to `1.1.1.1` ("Current DNS Server"), which cannot answer any of the 37 `.lan` names. Any r9 process that needs a `.lan` name can fail silently. This is the same failure a second AdGuard would cover (Q9). Not fixed; pinned hosts entries are the workaround for Wings.

**Moved 2026-09-21 04:53-04:57 (about 4 minutes total, nobody connected: no joins in the previous 6 hours):** `bedrock-tailscale` (rehearsal), `bedrock-public`, then `test-paper`. Each: graceful stop from the panel's Wings call, tar backup (`/home/jay/mc-backup-*.tar.gz` on production, mode 600: 82, 87 and 239 MB; keep at least a week), the panel's own transfer (helper: `scripts/pterodactyl-server-move.php`), start, ping. All three transfers `successful=1`, copy sizes identical to the source (321M, 323M, 260M). Verified with real protocol pings on r9: Bedrock `Dedicated Server` (25501) and `MediaHub Public` (25502), Java Paper 1.21.11 (25500, world loaded, no errors, 1.24 GB). Pterodactyl removed the source files on production, so the tar backups are the only other copy.

**Router forward moved by Jay (05:0x) and verified through the public address** (a loopback test from inside the LAN, so a friend joining from outside is still the definitive check): Java 25500 and Bedrock 25502 answer with the r9 servers; 25501 also answers publicly, although that server (`bedrock-tailscale`) was named as tailnet-only. Jay (2026-09-21): not concerned about that server being reachable, so the forward stays; closed. Unforwarded control ports (25599/25598) correctly failed. Tailnet players of `bedrock-tailscale` use `mediahub-r9.tail3b4ccf.ts.net:25501`; the Faithful pack URL (`mcpacks.lan` on production's Caddy) is unchanged and is not reachable for players outside the LAN/tailnet (pre-existing).

**Chapter closed 2026-09-21.** Production memory afterwards: 22 GB available (was 20 GB); swap 3.5 of 4.0 GB (swap shrinks slowly). Tar backups stay a week. **Prioritisation (2026-09-21):** fix r9's DNS drift first (small; a prerequisite for hosting more services there), then batch 2 in this order: Stirling PDF (stateless), Jellyfin (+ decide on retiring RomM), Immich last (database and photos). Backlog: Faithful pack reachability (the pack is optional, `require-resource-pack=false`), an outside-the-network friend test, and the second AdGuard (Q9).

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
| Q9 | Second AdGuard on the x51 as DNS failover? | Open. AdGuard holds 37 `.lan` rewrites that public DNS cannot answer; production reboots automatically Wed and Sun at 03:00, so `.lan` names and ad-blocking drop for a few minutes each time. Moderate value, low urgency; needs the two configs kept in sync. **Update 2026-09-21:** r9's resolver is sticky (never returns to the first-listed server after a failure), so any AdGuard outage (production's Wed/Sun 03:00 reboot) leaves r9 on `1.1.1.1` with no `.lan` names. A stopgap timer on r9 (`scripts/r9-resolved-prefer-adguard/`, every 2 min) returns it to AdGuard once AdGuard answers. With a second AdGuard, drifting to the second one would be harmless, which is the structural fix. |
| Q10 | AdGuard rate limit is 20 queries per second per /24 (whole LAN in one bucket) | **Found 2026-09-21:** a 100-query burst from r9 got 20 answers and 80 silent drops (EDNS0 is fine). Any household burst above 20 q/s is dropped for everyone; r9's resolver logged 7 'degraded feature set' events for `192.168.0.21` in a day. Fix is a live change in the AdGuard UI (Settings > DNS settings > Rate limit), no restart. Status: **done, see D5** (20 to 500, applied by Jay, verified 100/100, mirrored by hand). |
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
| 2026-09-21 | Phase 3 rehearsal passed on r9: GPU transcodes (incl. HDR tone mapping, 2 concurrent streams) confirmed; pool, NFS export from production and NVIDIA runtime set up. See 6b. Rehearsal container stopped. |
| 2026-09-21 | Phase 3 cutover done: Plex now runs on r9 (same server identity), Caddy and router port-forward repointed, remote access and GPU transcode verified. See 6c. |
| 2026-09-21 | Post-cutover: Live TV codec fix, EPG healthcheck moved to r9, rehearsal removed (server and files), r9 reboot test passed. Phase 3 complete; production's Plex kept stopped as rollback. |
