# OS updates & reboots: decision record (2026-09-25)

## Why this exists
Early on, the fleet went about 3 months without updates. The fix at the time was a root cron on production:
`0 3 * * 0,3 apt update && apt upgrade -y && reboot`. It rebooted twice a week whether or not anything needed it,
and nothing checked the machine afterwards. On Wed 2026-09-23 03:04 that reboot brought Threadfin back with **no
Docker network attached**, and Plex Live TV was dead for about 2.5 days. Uptime Kuma posted "down", but no one saw it.
Meanwhile r9 and staging never rebooted at all. Staging had a kernel update sitting there waiting on a reboot.

What already worked: Ubuntu's `unattended-upgrades` installs **security** updates daily on all three machines (no reboot).

## Requirements (Jay, 2026-09-25)
**Must do**
1. Security updates get installed *and actually take effect*.
2. A machine reboots only when an update requires it, inside an overnight window.
3. After any reboot the machine checks itself, auto-fixes known-safe problems, and reports to Discord.
4. Cover all three machines: production, r9 and staging. Staging is **not** being retired; it's the AdGuard replica and future cameras.

**Must not**
- Interrupt someone watching Plex. (Minecraft and Sunshine emulator sessions: nice-to-have, later.)
- Take two machines down the same night.
- Break things silently.

**Allowed**: non-security OS updates may install automatically.

## Options considered
| Option | Verdict |
|---|---|
| Keep the twice-weekly upgrade+reboot cron | Rejected: reboots without need, no post-reboot check (Threadfin), only covers production |
| Fully manual | Rejected: this is how the 3-month gap happened |
| A container that updates the host | Rejected: a container can't safely patch the machine it runs on |
| Canonical Landscape | Rejected: too heavy for 3 machines |
| **unattended-upgrades (daily) + per-machine maintenance window that reboots only if required and nobody is watching + post-boot health check + Ubuntu Pro Livepatch** | **Chosen** |

## Design
- **A. Updates, daily and automatic:** `unattended-upgrades` widened from security-only to all regular Ubuntu updates
  (`system/52fleet-unattended-upgrades` -> `/etc/apt/apt.conf.d/`). **Docker is excluded.** A Docker upgrade restarts
  every container, which could cut off a Plex stream mid-morning, so it's applied only in the maintenance window.
  Production's `needrestart` already never restarts Docker; r9 and staging don't have needrestart.
- **A2. NVIDIA driver packages are `apt-mark hold` on all three machines and never update automatically, not even
  in the window** (changed during the build, 2026-09-25). Ubuntu retires driver branches by turning the old
  branch's packages into transitional ones that pull in the next branch: staging's "535 security update" would have
  half-installed 580. The window posts held driver updates to Discord for a deliberate upgrade, same rule as containers.
  Other holds (e.g. `sunshine` on production) are respected.
- **B. Ubuntu Pro (free, up to 5 personal machines):** Livepatch applies most kernel security fixes without a reboot,
  and ESM extends security coverage. It needs Jay's token. It changes nothing about the desktop or UX.
- **C. Maintenance windows, one night per machine:** production Tue, r9 Thu, staging Sat, 02:00-05:00. Reboot only if
  `/var/run/reboot-required` exists or Docker/NVIDIA updates are pending, **and** nobody is streaming on Plex
  (production counts too: Live TV and the prod-internal media branch come from it). If someone is watching, it
  retries every 30 min until the window closes, then waits a week.
- **D. Health check:** `scripts/fleet-healthcheck.sh` runs after every boot (`systemd/fleet-healthcheck.service`,
  3 min delay) and every 30 min (cron `4,34`). It checks the required mounts (plus the D12 `lookupcache=none` fix),
  containers that were running before a reboot, containers running with no network, crash loops/unhealthy
  containers, AdGuard DNS, per-host endpoints, and the Plex Live TV tuner. Auto-fixes only these: mount a missing fstab
  mount, start a container that was running before the reboot, recreate a container with no network. Containers
  stopped on purpose are never touched (it compares against a snapshot of what was actually running). It posts to
  Discord `#service-outages` as "Fleet Health": always after boot, otherwise only when the problem set changes.
  Tested 2026-09-25 with a throwaway container: network loss -> recreated; missing after a simulated reboot -> started;
  deliberately stopped -> left alone.
- **E.** The twice-weekly cron is removed once C is live.

## Risks accepted
- Regular (non-security) updates occasionally misbehave. This is rare on LTS, and the health check reports within minutes.
- Kernel fixes may wait up to a week for the window; Livepatch covers most of that gap.
- The health check itself can be wrong, so it only auto-fixes a short list of safe cases and reports everything else.

## Status
- [x] D. Health check live on all three machines (2026-09-25)
- [x] C. Maintenance windows: `scripts/maintenance-window.sh`, root cron `0,30 2-4 * * <dow>` (prod Tue=2, r9 Thu=4,
      staging Sat=6). Dry-run tested on all three (`--dry-run`); production and r9 correctly deferred while a Plex stream
      was active. First real runs: staging Sat 2026-09-26, production Tue 09-29, r9 Thu 10-01.
- [x] A. unattended-upgrades widened, Docker/NVIDIA excluded (verified via `unattended-upgrade --dry-run` log)
- [x] A2. NVIDIA driver packages held on all three machines
- [ ] B. Ubuntu Pro attached (needs Jay's token)
- [x] E. Old `apt upgrade && reboot` cron removed from production (2026-09-25)
- [ ] Later: extend the "nobody is using it" check to Minecraft (Pterodactyl on r9) and Sunshine/Moonlight sessions
