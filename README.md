# mediahub-configs

Configuration backup for **mediahub-production** — a self-hosted personal cloud, media server, and household app platform running on a Dell OptiPlex 3050.

What started as "Plex + the arr stack" has grown into ~47 containers covering media, photos, cloud storage, password management, recipes, a gym tracker, book/audiobook reading, IPTV Live TV, LAN monitoring, and a retro-gaming/streaming setup. This README tracks the current state; `docs/` holds the incident writeups and design decisions behind it.

---

## Hardware

- **Machine:** Dell OptiPlex 3050 (Intel i7-7700 @ 3.6GHz, 4C/8T, 2017) — 32GB DDR4 (runs at 2400 MT/s, controller-capped)
- **GPU:** NVIDIA Quadro P400 (2GB VRAM) — passed through to Plex (`runtime: nvidia`) for hardware transcoding, active since 2026-07. Comfortable headroom for several concurrent Live TV transcodes; VRAM (not compute) is the eventual ceiling.
- **Boot drive:** NVMe SSD (Ubuntu 24.04), ~1.1 GB/s write / ~2.9 GB/s read — also hosts download staging (see Storage Layout)
- **Media drive:** 12TB internal HDD, mounted at `/mnt/internal`
- **NAS:** UGREEN NASync DH4300 Plus (192.168.0.23, RK3588 ARM64), mounted at `/mnt/nas` via NFS. **Single 24TB drive in a RAID1 pool with no second member — no actual redundancy.** ~22TB usable.
- **Unified pool:** `/mnt/media` — mergerfs overlay combining internal HDD + NAS; all apps use this path
- **Bluetooth:** TP-Link UB500 USB dongle (RTL8761BU, BT 5.1) — added for arcade/game controller pairing

---

## How It All Connects

```
Internet
   │
   └── Tailscale VPN (subnet router — exposes 192.168.0.0/24)
          │
          └── mediahub-production (192.168.0.21 / Tailscale 100.104.43.6)
                 │
                 ├── AdGuard Home (port 53) — LAN DNS, blocks ads, rewrites *.lan → 100.104.43.6
                 │
                 ├── Caddy (reverse proxy) — handles all *.lan HTTPS with internal TLS certs
                 │
                 ├── Authentik — SSO login provider (Jellyfin, RomM)
                 │
                 └── ~47 Docker containers, deployed straight from this repo (Portainer is a UI only — see below)
```

**The *.lan domain flow:**
1. Your device queries AdGuard for `plex.lan`
2. AdGuard rewrites it to `100.104.43.6` (Tailscale IP of mediahub)
3. Caddy receives the request and reverse proxies it to the right container
4. TLS cert is issued by Caddy's local CA (installed system-wide)

**Docker containers and *.lan domains:**
Docker containers cannot route to `100.104.43.6` — it's a host-local Tailscale address unreachable from the Docker bridge network. Uptime Kuma works around this via `extra_hosts` entries in its docker-compose that map all `.lan` domains directly to Caddy's Docker IP (`172.18.0.200`). Caddy is pinned to that IP via `ipv4_address: 172.18.0.200` in its docker-compose. Any new `.lan` service added to the Caddyfile also needs an `extra_hosts` entry added to Uptime Kuma's compose file.

**Portainer's role changed 2026-08-23:** every stack now deploys from `docker-compose.yml` in this repo — Portainer no longer holds the authoritative copy of anything. It's kept purely as a management UI (logs, console, Recreate button, phone access). **Never redeploy from Portainer's "pull and redeploy" button** — its copies are stale and deleting a stack entry there still stops/removes the live containers. Deploy with `docker compose up -d` from the stack's directory in this repo. See `docs/portainer-to-repo-migration.md`.

**Tailscale is set up with:**
- Subnet routing: advertises `192.168.0.0/24` (approved in Tailscale admin console)
- `--accept-dns=false` on the server itself (prevents a DNS loop since mediahub IS the DNS server)
- Split DNS on client devices: routes `.lan` queries → AdGuard at `100.104.43.6`
- **Only Pterodactyl is exposed to the open internet.** Plex is shared with people outside the tailnet (accepted, since it needs to work for guests without Tailscale installed) via port-forward/relay; everything else is tailnet+LAN only. Full reasoning in `docs/network-architecture.md`.

---

## Storage Layout

| Path | What it is |
|------|-----------|
| `/mnt/internal` | 12TB internal HDD (ext4) — cold storage, existing library |
| `/mnt/nas` | UGREEN NAS share over NFS (22TB, single-drive, **no redundancy**) — active library + finished downloads |
| `/mnt/media` | mergerfs pool combining both — all Docker apps mount this as `/data` |
| `/srv/downloads/incomplete` | **NVMe**, not the pool — SABnzbd's in-progress download staging |
| `/mnt/media/downloads/complete` | Finished downloads, on the pool |

**mergerfs policy:** most-free-space (`category.create=mfs`) — new writes go to whichever drive has more room.

**Critical mount option: `cache.files=off`.** The mount ran for months with `cache.files=partial`, which capped all pool writes at ~13% of the NAS's real throughput (4 MB/s) and was repeatedly misdiagnosed as a network/NAS problem. Switching to `cache.files=off` (direct I/O, skips FUSE's double-caching) restored full speed — reads were unaffected throughout. **Do not set this back to `partial` or `auto-full`.** Full writeup: `docs/mergerfs-cache-files-off.md`.

`cache.files=off` also blocks `mmap()` on `/mnt/media` — this has broken qBittorrent's resume-data handling and Calibre's SQLite access in the past (surfaces as `ENODEV` or a misleading `EACCES`/"disk I/O error"). **No databases belong on the pool.** Calibre's library, for example, lives on `/mnt/internal` directly for this reason.

**Why in-progress downloads live on the NVMe instead of the pool:** SABnzbd writes thousands of small article fragments while a job is downloading, which is exactly the write pattern the pool used to handle worst. This is no longer load-bearing now that `cache.files=off` is fixed, but keeping small random I/O off the pool is still preferable, so it hasn't been moved back. **Finished downloads and the library must stay on the same device** so *arr apps hardlink on import instead of physically copying — check with `stat -c%d`.

---

## Services

### Media & Requests
| Stack | What it does | Domain |
|-------|-------------|--------|
| Plex | Media server + Live TV/DVR (hardware-transcoded via the P400) | plex.lan |
| Jellyfin | Media server (SSO via Authentik) | jellyfin.lan |
| Sonarr / Radarr / Lidarr / Prowlarr / Bazarr | TV / movie / music management + indexer aggregation + subtitles (one combined stack, `arr-stack`) | sonarr.lan / radarr.lan / lidarr.lan / prowlarr.lan / bazarr.lan |
| Seer | Request management (Overseerr fork) | seer.lan |
| Aurral | Music discovery/request frontend → feeds Lidarr → SAB/qBittorrent (not a downloader itself) | aurral.lan |

### Downloads
| Stack | What it does | Domain |
|-------|-------------|--------|
| SABnzbd | Usenet downloader | sabnzbd.lan |
| qBittorrent | Torrent client (behind Gluetun VPN) | qbittorrent.lan |
| Gluetun | VPN kill-switch for qBittorrent | — |
| Unpackerr | Extracts completed downloads | — |

### Live TV / IPTV
| Stack | What it does | Domain |
|-------|-------------|--------|
| Threadfin | IPTV playlist → Plex Live TV/DVR bridge, channel mapping | threadfin.lan |
| iptvorg-epg | Backup EPG data source (iptv-org), used when the primary provider's guide data goes stale | — |

### Books & Audio
| Stack | What it does | Domain |
|-------|-------------|--------|
| Audiobookshelf | Audiobook + podcast server | audiobookshelf.lan |
| Calibre | Ebook library management (web + content server) | calibre-web.lan / calibre-content.lan |
| Bookshelf | Ebook + audiobook reader | bookshelf-ebooks.lan / bookshelf-audiobooks.lan |
| rreading-glasses | Self-hosted book metadata provider (backs Bookshelf/Calibre) | — |

### Cloud, Household & Games
| Stack | What it does | Domain |
|-------|-------------|--------|
| Immich | Photo backup | immich.lan |
| Nextcloud | Personal cloud storage | nextcloud.lan |
| Vaultwarden | Password manager (Bitwarden-compatible) — self-signup deliberately left open, tailnet is the gate | vaultwarden.lan |
| Obsidian-sync | CouchDB + LiveSync backend for Obsidian vault syncing | obsidian-sync.lan |
| Mealie | Recipe manager | mealie.lan |
| openGym | Gym/workout tracker (passkey-only auth, no password/recovery) | gym.lan |
| Stirling PDF | PDF toolkit | stirling.lan |
| RomM | Game ROM library (SSO via Authentik) | romm.lan |
| Pterodactyl | Minecraft server panel — **the only service exposed to the open internet** | pterodactyl.lan |

### Infrastructure & Monitoring
| Stack | What it does | Domain |
|-------|-------------|--------|
| Authentik | SSO / identity provider | auth.lan |
| AdGuard Home | DNS + ad blocking | adguard.lan |
| Caddy | Reverse proxy + TLS | — |
| Portainer | Docker management UI (containers only — not the source of truth for any compose file) | portainer.lan |
| Homepage | Dashboard | homepage.lan |
| Uptime Kuma | Uptime monitoring | uptimekuma.lan |
| WUD (What's Up Docker) | Update-availability checks, feeds Homepage's update tiles | wud.lan |
| Glances / Netdata | System stats / monitoring (two tools, some overlap) | glances.lan / netdata.lan |
| Scanopy | LAN device scanner/inventory | scanopy.lan |

**Update policy:** no Watchtower or unattended auto-updates — WUD surfaces what's outdated, but bumps are deliberate, scoped, and reversible. Full-system `apt upgrade` + reboot runs Wed/Sun 3am via cron.

### Game Streaming & Emulation (host-level, not containerized)
An in-progress project to make mediahub double as a retro-gaming/streaming box: Sunshine (host-installed) streams to a Moonlight client on the ceiling-mounted projector, with ES-DE as the frontend over RetroArch and Dolphin cores. Runs headless on the host (not in Docker) using the same Quadro P400. Configs live in `es-de/`, `retroarch/`, `sunshine/`; design notes and phased plan are in `docs/` and `~/mediahub-arcade.md`.

---

## What's in This Repo

```
mediahub-configs/
├── stacks/          # Docker Compose file for every service — authoritative since 2026-08-23
├── caddy/           # Caddyfile (all reverse proxy rules)
├── adguard/         # AdGuardHome.yaml (DNS rules, rewrites, blocklists)
├── system/          # fstab, docker daemon config, sudoers, Xorg/nvidia headless configs
├── systemd/         # Custom systemd units (wings cert reloader)
├── cron/            # Root crontab
├── scripts/         # Health-check, watch, and arcade helper scripts run by cron/systemd
├── docs/            # Incident writeups, architecture decisions, and gotchas (read before changing storage, mergerfs, or networking)
├── es-de/ retroarch/ sunshine/  # Retro-gaming/streaming configs (host-level, see above)
├── speedtest/       # Static speed-test page
├── ufw-rules.txt    # Firewall rules
└── README.md        # This file
```

**What's NOT here (intentionally):**
- `.env` and per-stack `.env` / `stack.env` files — hold secrets and machine-specific values. Copy each `.env.example` and fill it in. Never commit real `.env` files — they're gitignored.

---

## Fresh Install

> **Server OS requirement:** The machine running the containers must be **Ubuntu 24.04** (or another Debian-based distro). `setup.sh` uses `systemctl`, `update-ca-certificates`, and Linux path conventions — it won't run on Mac or Windows. You don't need to *be* on Ubuntu though; just SSH in from whatever computer you're on.

Clone this repo and run the setup script:

```bash
git clone https://github.com/alleyneja/mediahub-configs.git
cd mediahub-configs
./setup.sh
```

The first run creates `.env` from `.env.example` and exits. Fill in all values, then run `./setup.sh` again — it handles everything: directory structure, Docker network, config template processing, and starting all stacks.

### Prerequisites (install before running setup.sh)

```bash
# Required system packages
sudo apt update && sudo apt install -y nfs-common mergerfs

# Docker Engine (not docker.io — the Engine package includes the Compose plugin)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --advertise-routes=192.168.0.0/24 --accept-dns=false
# Then approve the subnet route in the Tailscale admin console
```

### After setup.sh completes

setup.sh automatically installs Caddy's CA cert into the system trust store and Jellyfin.

1. **Point your router's DNS** at `SERVER_IP`
2. **Tailscale admin console:** approve the subnet route and set Split DNS for `.lan` → `SERVER_IP`
3. Visit `https://adguard.lan` — set your admin password (the config ships with no password)

### One-time system config (manual, not automated by setup.sh)

```bash
# Storage mounts (edit fstab to match your drive UUIDs and NAS IP first — and keep cache.files=off)
sudo cp system/fstab /etc/fstab
sudo mkdir -p /mnt/internal /mnt/nas /mnt/media
sudo mount -a

# Firewall
sudo apt install -y ufw
# Apply rules from ufw-rules.txt
# NOTE: published Docker container ports bypass UFW entirely (DOCKER-FORWARD
# runs ahead of ufw-* in the FORWARD chain). Host-networked services (e.g. Plex)
# are the only ones UFW actually governs — don't assume a UFW rule protects a
# published container port.

# Systemd units (Pterodactyl Wings cert reloader)
sudo cp systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wings-cert-reload.timer

# Cron
sudo crontab cron/root-crontab
```

---

## Updating This Repo

Every stack's `docker-compose.yml` in this repo is the authoritative copy. To change a service:

```bash
cd ~/mediahub-configs
# Edit the compose file directly under stacks/<name>/
docker compose -f stacks/<name>/docker-compose.yml up -d

git add stacks/<name>
git commit -m "describe what you changed"
git push
```

**Do not** deploy from Portainer's UI — its stored copies are stale since the 2026-08-23 migration and its "pull and redeploy" / delete-stack actions can stop live containers out from under you. Portainer is fine for viewing logs and consoles.

**If WUD shows an update and you click Run:** it can carry over the *old* container's env/state instead of picking up the new image's own defaults. Always follow up with a plain `docker compose up -d` from the repo to get a clean redeploy.

**Adding a new service checklist:**
- [ ] Add the service's docker-compose to `stacks/<name>/`
- [ ] Add a reverse proxy block to `caddy/Caddyfile` (`<name>.lan { tls internal; reverse_proxy ... }`)
- [ ] Add a DNS rewrite in AdGuard: `<name>.lan` → `100.104.43.6`
- [ ] Add an `extra_hosts` entry to `stacks/uptime-kuma/docker-compose.yml`: `- "<name>.lan:172.18.0.200"`
- [ ] Recreate Uptime Kuma to pick up the new hosts entry: `docker compose up -d --force-recreate uptime-kuma`
- [ ] Add a monitor in Uptime Kuma for `https://<name>.lan`
- [ ] If it needs secrets, add a `.env.example` in its stack folder documenting every variable

---

## Where to Look for Deeper Context

`docs/` has grown into the real institutional memory for this box — incident writeups, root-cause analyses, and architecture decisions that don't fit a README. Worth reading before touching storage, networking, or anything that's bitten us before:

- `docs/network-architecture.md` — requirements-first network design, read before any hardware purchase
- `docs/mergerfs-cache-files-off.md` — the storage speed fix above, in full
- `docs/download-storage-layout.md` — why downloads are split across NVMe/pool
- `docs/portainer-to-repo-migration.md` — full migration history and per-stack gotchas
- `docs/arr-upgrade-policy.md`, `docs/radarr-minimum-availability.md`, `docs/sonarr-*` — *arr app quirks
- `docs/opengym-gym-lan-passkeys.md` — passkey auth tradeoffs (no recovery path, by design)
- `docs/tailscale-cellular-mtu-blackhole.md` — the MTU floor that must never go below 1280

---

## Key IPs

Configure these in `.env` before running `setup.sh`:

| Variable | Description | Example |
|---|---|---|
| `SERVER_IP` | LAN IP of this machine | `192.168.0.21` |
| `TAILSCALE_IP` | Tailscale-assigned IP | `100.104.43.6` |
| `TAILSCALE_HOSTNAME` | Tailscale MagicDNS hostname | `mediahub-production.tail3b4ccf.ts.net` |
