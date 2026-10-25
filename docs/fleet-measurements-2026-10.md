# Fleet measurements, October 2026 (service-placement review)

**Purpose:** a measured baseline of what `mediahub-i7` and `mediahub-r9` actually carry, so placement and
scaling decisions rest on numbers and not on assumptions. Tracked in `alleyneja/mediahub-issues#83`. This is
a measurement record, not a decision; decisions made from it get their own `D<n>` entry in
[fleet-architecture.md](fleet-architecture.md).

## Method

- Collector: `scripts/placement-metrics.py`, unit `systemd/placement-metrics.service` (root, read-only, low priority).
  One JSON line per minute per host in `/var/log/placement-metrics/<hostname>.jsonl`.
- Per line: CPU busy and iowait %, load, memory available, swap used and swap in/out per second, CPU package watts
  (RAPL, average and max of 1 s samples), GPU watts/utilization (nvidia-smi, 1 s samples), mount usage, and
  per-container CPU % and memory (`docker stats`, one snapshot per minute).
- Window: 2026-10-20 17:57 to 2026-10-24 23:43 CDT (4.2 days, about 6,050 samples per host). Includes some gaming
  on r9; SubGen backlog was mostly worked down (71 jobs on 10-20, 1-16 per day after).
- Re-run: `scripts/placement-metrics.py` is still running on both hosts. To analyze, copy the two `.jsonl` files and
  compute percentiles per field. Stop with `systemctl disable --now placement-metrics`.

## Results

| Measure | mediahub-i7 (i7-7700, 8 threads, 32 GB) | mediahub-r9 (9900X, 24 threads, 64 GB, RTX 5070 Ti) |
|---|---|---|
| CPU busy, mean / p95 / max (1-min) | 21% / 45% / 66% | 3.4% / 26% / 32% |
| iowait, mean / p95 | 3% / 12% | 0.3% / 1.4% |
| Load1, mean / p95 / max | 3.2 / 7.5 / 14.9 | 1.8 / 6.6 / 15.6 |
| RAM available | about 19 GB (steady) | about 53 GB |
| Swap | 4 GB, full all the time; swap in/out about 0 per second | 1-7.4 GB; one episode (below) |
| CPU package watts, mean / p95 | 28 / 44 | 52 / 109 |
| GPU | none that matters (Quadro P400) | mean 4% util, 31 W; 100 minutes above 100 W; max 201 W |
| Containers | 53 | 11 |

Largest container CPU users (mean % of one core): i7: netdata 12.9, flaresolverr 7.4, qbittorrent 7.2, bazarr 7.1,
aurral 6.6, lidarr-downloader 4.6. r9: plex 29.6, subgen 1.7. Largest resident memory: i7: sonarr about 2.7 GB;
r9: plex about 2.1 GB.

Disks (last sample): i7 `/mnt/internal` 86.8% (8.8 TB of 10.9 TB used); pool `/mnt/media` 66.2%; NAS 56.0%.

## Addendum: x51 added (2026-10-25)

The collector also runs on `mediahub-x51` (i7-4790, 16 GB, GTX 1650) from 2026-10-25 10:23 CDT, so all three servers are
tracked from then on; the tables above cover only i7 and r9 for 2026-10-20 to 10-24. First sample: CPU busy 6.8%,
CPU package about 6.5 W (peak 10.9 W), 13.8 GB RAM available, no swap use, load 0.28. Its power numbers are CPU package
only (RAPL). The GTX 1650 and the i7's Quadro P400 report power draw as `[N/A]`, so the collector now records GPU
utilization and memory with a null power value for those cards (same day, `num()` in the script).

## What the data says

1. **The i7 is not CPU- or RAM-starved.** It is bursty: load spikes to about 15 for a minute or two when
   lidarr-downloader, bazarr, aurral, threadfin or flaresolverr run at once, but 1-minute CPU never passed 66%.
   Full swap with no swap traffic means cold pages parked there, not pressure.
2. **The i7's real limit is storage:** `/mnt/internal` is 86.8% full.
3. **The r9 is about 97% idle on CPU** and its GPU is mostly idle. Plex is its biggest consumer.
4. **Power:** r9 CPU+GPU peaks reach 328 W within a single minute (48 minutes above 250 W, 11 above 300 W).
   Figures are CPU package plus GPU board only, excluding the rest of the system. The UPS is an APC BE600M1, 330 W.
   Most peak minutes show a GPU at 99-100% with the CPU nearly idle, which matches Whisper (SubGen) inference;
   a few evenings show GPU 15-70% with CPU 15-30% busy for hours, which looks like interactive use or gaming.
   Attribution is by signature only (see "Not established"). Jay recalls testing the PS4 and PS5 emulators around
   the interactive evenings (10-22), which fits those hours; the exact times were not recorded.
5. **r9 swap episode (10-21 15:33-15:36 and 19:33-19:37):** swap-out of 1.7-2 GB per minute while 50+ GB of RAM
   stayed available. The pages swapped belonged to idle container memory (Plex resident 5.6 to 3.4 GB, then 3.0 to
   1.2 GB; SubGen 696 to 276 MB). Each time, GPU idle power stepped up (16 W to 25-26 W) as if a graphical session had
   started, and `gdm-x-session` logged a display change at 15:34:54. Swap was never reclaimed and was cleared by the
   scheduled 02:00 reboot on 10-22. Probable cause: a graphical or game session start forcing reclaim. Not proven.
   Effect: Plex first-use slowness after the pages go cold. `vm.swappiness` is 60 on r9.
6. **Reboots:** r9 and i7 reboot at similar rates, almost all scheduled or shared power events. There are no crash
   dumps and no kernel NVRM/Xid/oops/MCE lines on r9 since 2026-09-20. D3's premise that r9 is the likeliest to crash
   was an assumption and the record does not support it.

## Not established (state this when citing these numbers)

- Only 4.2 days, one weekend; SubGen nearly idle, so SubGen-driven peaks are under-represented.
- Which exact minutes were gaming versus SubGen versus Plex. Container CPU is a once-a-minute snapshot, and
  SubGen's own log shows 135 "Transcribing" lines in the window but timestamps do not line up cleanly with the
  hot minutes. Sunshine's log restarts at each boot and holds no client history, so game times need to come from Jay.
- Wall-socket watts. RAPL and GPU board power exclude PSU losses and other components.
- The cause of the swap episodes (timing only).
- Growth rate of the library on `/mnt/internal` (not measured here).

## UPS (confirmed by Jay, 2026-10-24)

The UPS (APC BE600M1, 600 VA / 330 W) carries a monitor, x51, i7, r9 and the NAS. None of them has a USB data
link to it (no APC device in `lsusb` on i7, r9 or x51; no apcupsd or NUT installed), so its own load reading is
not available. Rough idle sum, **estimates except where noted**: r9 about 100 W (CPU+GPU 68 W measured at idle,
plus board, fans, drives and PSU loss), i7 about 80 W, x51 40-60 W, NAS 25-40 W, monitor 20-40 W: about 265-320 W
before any burst, against a 330 W limit. A SubGen or gaming burst on r9 adds roughly 100-250 W, so overload is
expected at the peaks listed above. Not measured: actual wall watts of x51, the NAS and the monitor.

## Headroom, for scaling decisions

| Resource | i7 | r9 | Source |
|---|---|---|---|
| CPU | about 80% free on average, about 55% free at p95 | about 97% free on average | measured |
| RAM | 19 GB available | 53 GB available | measured |
| GPU | none | RTX 5070 Ti, mostly idle | measured |
| Library disk | 1.6 TB free on `/mnt/internal` | n/a | measured |
| Pool | 12 TB free (NAS 9.8 TB) | n/a | measured |
| Network | gigabit, about 112 MB/s ceiling, shared i7/NAS/r9 | same | earlier notes, not re-measured here |
| UPS | BE600M1, 330 W | same | earlier notes |
| NAS drive | single 24 TB in a one-disk RAID1 pool | n/a | hardware notes, not re-verified here |

The binding constraints are the library disk, the single gigabit path to the NAS, the UPS, and the single-drive
NAS, not CPU or RAM on either machine.

## Follow-ups

- Attribute the power peaks with Jay's game times; decide on a UPS plan (see the UPS project note).
- Decide whether to lower `vm.swappiness` on r9 (reversible, needs its own decision record).
- Per-service table and challenge step (PROCESS.md) before any service moves.
