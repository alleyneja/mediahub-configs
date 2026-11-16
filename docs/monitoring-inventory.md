# Monitoring & measurement inventory

**This is the one place that lists every monitor, logger, sampler and checker in the fleet.** If you add, retire or move
one, edit this file in the same commit. Written 2026-11-15 after "what are we even monitoring?" took a multi-source hunt
(Kuma DB + 3 crontabs + systemd + ~/logs + Discord + issues).

Hosts: **prod** = production/i7 (`mediahub-production`), **r9** = `mediahub-r9`, **x51** = staging.
Scripts live in `scripts/` unless a path is given. Per-host logs: `~/logs/` (outside the public repo on purpose).

## How to find things again (the hunt, as a checklist)

1. `crontab -l` and `sudo crontab -l` on **each** host (prod, r9, x51), plus `systemctl list-units --type=service --state=running`.
2. `ls -t ~/logs` and `ls /var/log/placement-metrics`: every measurer leaves a log; the newest files are the live ones.
3. Uptime Kuma monitor list: `sudo sqlite3 -readonly /srv/docker/visibility/uptime-kuma/kuma.db "select id,name,type,active from monitor"`.
4. Discord alert channels (`docs/discord-alerts.md`); read them with the bot (`scripts/discord-bot.env`) to see what actually posts.
5. `gh issue list -R alleyneja/mediahub-issues` (labels `status:monitoring`); closed issues often name the script that closed them.

## A. Standing monitors (alert somewhere)

| What | Host | Schedule | Measures | Alerts to | Doc / issue |
|---|---|---|---|---|---|
| Uptime Kuma (46 monitors) | prod | 60 s | service up/down: 38 http (incl. #50 `Plex (server /identity)`, added 2026-11-15, catches a hung Plex that `/web` misses, and #51 `Plex Live TV (sessions API)`, added 2026-11-16, catches Live TV wedging while `/identity` still answers), 2 port, 1 keyword, 1 json-query (IPTV account), 4 push | #service-outages | `stacks/uptime-kuma`, `docs/discord-alerts.md` |
| Netdata (~400 alarms) | prod | continuous | CPU/RAM/disk/OOM/gluetun health; swap-% silenced Sep 25 | #resource-alerts | `stacks/uptime-kuma/netdata-health.d/` |
| `fleet-healthcheck.sh` | prod, r9, x51 | every 30 min + after boot | Caddy, AdGuard, Home Assistant and more (see the script header); posts only on change | #service-outages ("Fleet Health") | `docs/os-updates.md` |
| `integration-check.py` | prod | hourly :20 | *arr/Bazarr health, *arr->Plex, Seerr->Plex/Radarr/Sonarr, Immich version match, Loki freshness | Discord on change + Kuma push 49 | `docs/log-monitoring.md`, #71 |
| Loki + Grafana + Alloy | r9 (Loki/Grafana), prod+r9+x51 (Alloy) | continuous | container logs, 90-day retention | (feeds the digest) | `docs/log-monitoring.md` |
| `log-digest.py` | prod | Sun 09:00 | repeating container errors, new vs fixed | #service-logs + rolling issue | `docs/log-monitoring.md`, #72 |
| `permissions-monitor.sh` | prod, r9 | every 15 min | mode 000/777 files across the pool (forensics) | #permissions-issues (daily digest 08:07 via `permissions-digest-send.sh`) | `docs/permissions-monitor.md`, D12 |
| `permissions-canary.sh` + `permissions-canary-rollup.sh` | prod | :11/:41 and daily 08:13 | NAS inode-eviction canary (is the lookupcache fix holding) | #permissions-issues | `docs/permissions-monitor.md` |
| `lidarr-ytdl-audit.py` | prod | every 15 min | wrong-length YouTube-fallback tracks (deletes + bans) | #media-alerts | #80 |
| `stacks/arr-stack/scripts/notify-audio-mismatch.sh` | prod | on import | grabbed vs embedded audio language | #media-alerts | |
| `update-restart-report.sh` | prod, r9, x51 | after daily unattended-upgrades | which services the updates restarted | #service-outages | `docs/os-updates.md`, #53 |
| `adguard-sync-x51.py` | prod | hourly :17 | AdGuard replica sync | Kuma push 44 | |
| `immich-backup.sh` | prod | 02:30 | Immich DB dump | Kuma push 43 | |
| `subtitle-enforce.py` (gate + auto-replace, movies) | prod | every 15 min | runs the gate (rule v1) then replaces new movie FAILs via `subtitle-replace.py` (cap 5/tick); posts every replacement, unresolved and needs-review title to #media-alerts; TV stays report-only (digest post); `~/subtitle-sweep/PAUSE` stops it; state `~/subtitle-sweep/enforce-state.json`, ledgers `~/subtitle-sweep/` and `~/logs/subtitle-gate/`; log `~/logs/subtitle-gate/enforce-cron.log`; record `docs/subtitle-enforce-mode.md` |
| `offsite-backup.sh` | prod | 03:00 | restic -> Google Drive | Kuma push 48 | #34 |
| `issues-report.sh` | prod | monthly | tracker age/volume report | #service-outages | |
| `check-fleet-identity.sh` | prod | monthly | PUID/PGID drift in compose files | log only | |
| `fleet-inventory.sh` | prod | daily 07:30 | OS/kernel/Docker/container versions -> **private** issues repo | git history | |
| `nextcloud-save-watch.py` | prod | **systemd service**, always on | Nextcloud DAV save failures (403s invisible to nextcloud.log) | `~/logs/nextcloud-save-watch*.log` | #12 (closed); see section D |
| `plex-epg-healthcheck.sh` | r9 (root `@reboot`; disabled on prod) | boot | stale Plex EPG state | log | `docs/` Plex Live TV |
| WUD, Glances | prod | continuous | image-update visibility, host stats | Homepage tiles | |

## B. Open investigations (measure now, decide later)

| What | Host | Schedule | Output | Issue | Status 2026-11-15 |
|---|---|---|---|---|---|
| `placement-metrics.py` (systemd) | prod, r9, x51 | every 60 s, forever | `/var/log/placement-metrics/<host>.jsonl` (r9 36 MB, x51 17 MB, grows; **no rotation noted**) | #83 | window "3-7 days" long exceeded; needs a read-out and a stop decision |
| `netmon.py` (+ `--sample`, `netmon-report.py`) | prod | 30 min + every minute | `~/logs/netmon/` speed, latency, modem, buffering events | #48, #63 | live |
| `sshmon.py` + `lossmon.py` | prod | every minute | `~/logs/sshmon/` | #81 | live; **review was due 2026-11-01** |
| `livetv-feed-sampler.py` | prod | every 10 min, self-expires 7 d | `~/logs/livetv-sampler/samples.csv` | #87 | expires ~2026-11-15 20:08; 818 measured cycles, direct feed starved in 2; skips cycles while a viewer is watching |
| `livetv-session-tap.py` (systemd) | r9 | every 1 s, always on | `~/logs/livetv-session-tap/tap.csv`: per-second kB/s, RTT, retransmits for each real Live TV stream (Plex Transcoder's outbound :80 connections to the provider's video server: Threadfin only hands Plex a redirect at tune time, so Threadfin restarts do not matter). No extra connection, 100% of viewing time; rotates at 200 MB; `--summary` | #87 | started 2026-11-15; replaces the 2% sampler as the evidence source |
| `livetv-idle-probe.py` (systemd) | prod | always on; holds 1 provider stream for 3 min in every 10, only while nobody is watching (~240 GB/week; the stream is ~1.3 MB/s) | `~/logs/livetv-idle-probe/probe.csv`, per-second kB/s on WESH. Starts after 5 min of 0 active connections, rests 7 min between slices, closes the instant `active_cons` >= 2. Known cost: a second viewer starting within ~5 s of the first can get a Playback error. Kill: `touch ~/logs/livetv-idle-probe/STOP` | #87 | started 2026-11-15; pairs with the session tap (tap = someone watching, probe = nobody watching) |
| `cc-survey.py` | prod | one-off | `~/logs/cc-survey/` | #66 | on hold |

## C. On-demand diagnostics (run by hand)

| Script | Purpose |
|---|---|
| `pathtest.sh` | reach the server from a phone WITHOUT Tailscale (temp web server on TCP 41641, firewall open only to T-Mobile range, auto-teardown 15 min) to separate tunnel slowness from path slowness; run with sudo |
| `netcheck.sh` | repeatable network baseline; `--compare` last two runs |
| `ts-watch.sh` | sample tailnet path (direct vs DERP) during a transfer |
| `plexload.py`, `vramprobe.py` | synthetic Plex transcode load / VRAM peak on r9 (abort if a real viewer appears) |
| `session-activity.py` | is anyone on this machine (Sunshine, Plex) before a reboot (#42) |
| `permissions-repro-test.sh`, `nas-log-snapshot.sh` | D12 reproduction + NAS evidence pull |
| `stacks/subgen-r9/scripts/` (`wer_cer.py`, `score_subtitles.py`, `score_sweep.py`) | subtitle WER/CER scoring |
| `stacks/arr-stack/scripts/bazarr-sync-backlog.py` | Bazarr sync backlog |

## D. Outside the repo (found 2026-11-15; decide: adopt, or delete when done)

| Path | What | Note |
|---|---|---|
| `~/nextcloud-save-watch.py` | **the running service executes this copy**; byte-identical to `scripts/nextcloud-save-watch.py` | point the unit at the repo copy, delete the home copy |
| r9 `~/loadtest/` | Plex load-test + VRAM probe results (#43/#48): data, keep until those issues close | keep |
| `~/nextcloud-backup.sh`, `~/vaultwarden-backup.sh`, `~/authentik-backup.sh` | nightly backups at 02:35/02:40/02:45 | **not in repo, no Kuma heartbeat**; `calibre-backup.sh` and `immich-db-backup.sh` are symlinks into the repo |
| `~/subtitle-audit-tools/`, `~/talkarr-trial/`, `~/talkarr-scratch/` | subtitle sync measurement + Talkarr trial (#73); `talkarr-trial` container (127.0.0.1:9494, read-only media, Manual mode) is still running | active work; remove the container + scratch when the trial concludes |

Archived (not in use, kept for reference): `scripts/archive/verify-nvfbc.sh` (old production P400/NvFBC post-reboot check; streaming now on r9) and `scripts/archive/sonic-analysis-loop.sh` (May 2026 Plex sonic-analysis restart loop; hardcoded to production Plex paths, retired Nov 15).

## E. Known gaps (no monitor yet)

- Nextcloud, Vaultwarden, Authentik, Calibre backups, r9 saves backup: no heartbeat, so a silently failing backup is invisible.
- NAS drive health / capacity: nothing watches it (#31).
- Live TV feed quality after the sampler expires; Schedules Direct EPG freshness.
- Netdata is confirmed on prod only (r9/x51 Docker containers were not inspected); `placement-metrics` runs on all three but is a collector, not an alerter.
- `subtitle-enforce.py` (runs the gate every 15 min): no Kuma heartbeat yet (creating a push monitor means stopping Kuma; decision pending), so a silently stopped gate is only visible in `--summary` and `~/logs/subtitle-gate/enforce-cron.log` or `~/logs/subtitle-gate/gate.log`.
- Unpackerr: no API; only the weekly digest sees its failures.

## F. Discord channels

`#service-outages` (Kuma, Fleet Health, update restarts, monthly report), `#resource-alerts` (Netdata), `#media-alerts`
(Lidarr audit, audio mismatch, discovery), `#permissions-issues` (permissions + canary), `#service-logs` (weekly digest),
`#home-automation` (unwired), `#joyboy-alerts` (retired, still receives old Kuma posts).

## Change log
- 2026-11-15: created. Deleted the Sep 25 D12 leftovers (pilot/sentinel/permlab/reboot-test files in prod `~/logs`, r9 `crontab.bak.2026-09-25`, x51 `scripts.retired-20260925`); D12 is closed and nothing referenced them.
- 2026-11-15: `pathtest.sh` moved into `scripts/`; `verify-nvfbc.sh` and `sonic-analysis-loop.sh` archived in `scripts/archive/` (Jay approved).
- 2026-11-15: deleted burn-in leftovers (x51 gpu-burn/logs/thermal-monitor, r9 `~/burnin`), the stale x51 setup.sh diff (OCR seeding is in the repo) and the duplicate home `netcheck.sh`. Kept r9 `~/loadtest`.
- 2026-11-15: added `subtitle-gate.py` (report-only subtitle quality gate, #73).
- 2026-11-15: `ts-watch.sh`: kept the newer home version (1 s sleep, was 2 s) in the repo; home path is now a symlink. The five `~/arcade-*` scripts (all identical to repo copies) are now symlinks into `scripts/`, so documented `~/arcade-*` paths still work. r9 got the same symlinks (it lacked them, so `arcade-bootcheck.sh` could not find `arcade-screenshot.sh`).
- 2026-11-15: added Kuma monitor 50 `Plex (server /identity)` (`https://plex.lan/identity`, 60 s, Discord). DB backup `kuma.db.pre-plex-identity-20261115` (730 MB) is in `/srv/docker/visibility/uptime-kuma/`; delete after a few days.
- 2026-11-15: added `livetv-session-tap.py` (r9 systemd service): passive per-second log of real Live TV streams (#87), because the 10-minute sampler covered ~2% of the week.
- 2026-11-15: added `livetv-idle-probe.py` (prod systemd) for idle-time coverage; both Live TV loggers judge a rolling 10 s average vs 1000 kB/s because the feed is bursty (MBs every 6-8 s), so per-second rates are not stalls.
- 2026-11-15: tap finally pointed at Plex Transcoder's outbound :80 connection to the provider's video server (verified on a live stream; two earlier targets were wrong); stalls judged per session (30 s average under half the session mean, plus silent gaps), because the feed is bursty and bitrate varies by channel; idle probe set to 3 min on / 7 min off and left STOPPED (`systemctl stop livetv-idle-probe`) until Live TV tuning was confirmed working. Plex Live TV wedged ~18:53-20:48 (tunes never reached Threadfin; `/livetv/sessions` hung); `docker restart plex` cleared it.
- 2026-11-16: added Kuma monitor 51 `Plex Live TV (sessions API)` (`http://192.168.0.22:32400/livetv/sessions`, X-Plex-Token header stored in the monitor, 10 s timeout, 60 s, Discord); backup `kuma.db.pre-livetv-monitor-20261116` (delete with the Nov 15 one in a few days). Tap now also logs `ooo_total` (out-of-order packets in = download-side loss; `retrans` only counts our sends). One-shot `livetv-game-probe` (systemd-run timer, prod, 2026-11-16 18:45, ESPN HD, up to 3.5 h, yields to viewers) captures a busy sports feed; output `~/logs/livetv-game-probe/probe.csv`, read with `PROBE_LOGDIR=~/logs/livetv-game-probe livetv-idle-probe.py --summary`.

- 2026-11-16: subtitle gate moved from Tue/Thu/Sat 04:45 report-only to `subtitle-enforce.py` every 15 minutes (auto-replace for movies, Jay's go; crontab before: `~/subtitle-sweep/crontab.before-enforce`).