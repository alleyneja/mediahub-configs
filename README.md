# mediahub-configs

Configuration backup for **mediahub-production** — a self-hosted personal cloud and media server running on a Dell OptiPlex 3050.

---

## Hardware

- **Machine:** Dell OptiPlex 3050 (Intel i7-7700, 32GB RAM)
- **Boot drive:** NVMe SSD (Ubuntu 24.04)
- **Media drive:** 12TB internal HDD, mounted at `/mnt/internal`
- **NAS:** UGREEN NAS (192.168.0.23), 22TB free, mounted at `/mnt/nas` via NFS
- **Unified pool:** `/mnt/media` — mergerfs overlay combining internal HDD + NAS; all apps use this path

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
                 ├── Authentik — SSO login provider used by Jellyfin
                 │
                 └── ~45 Docker containers managed via Portainer
```

**The *.lan domain flow:**
1. Your device queries AdGuard for `plex.lan`
2. AdGuard rewrites it to `100.104.43.6` (Tailscale IP of mediahub)
3. Caddy receives the request and reverse proxies it to the right container
4. TLS cert is issued by Caddy's local CA (installed system-wide)

**Docker containers and *.lan domains:**
Docker containers cannot route to `100.104.43.6` — it's a host-local Tailscale address unreachable from the Docker bridge network. Uptime Kuma works around this via `extra_hosts` entries in its docker-compose that map all `.lan` domains directly to Caddy's Docker IP (`172.18.0.4`). Caddy is pinned to that IP via `ipv4_address: 172.18.0.4` in its docker-compose. Any new `.lan` service added to the Caddyfile also needs an `extra_hosts` entry added to Uptime Kuma's compose file.

**Tailscale is set up with:**
- Subnet routing: advertises `192.168.0.0/24` (approved in Tailscale admin console)
- `--accept-dns=false` on the server itself (prevents a DNS loop since mediahub IS the DNS server)
- Split DNS on client devices: routes `.lan` queries → AdGuard at `100.104.43.6`

---

## Storage Layout

| Path | What it is |
|------|-----------|
| `/mnt/internal` | 12TB internal HDD (ext4) — cold storage, existing library |
| `/mnt/nas` | UGREEN NAS share over NFS (Btrfs, 22TB free) — active library + downloads |
| `/mnt/media` | mergerfs pool combining both — all Docker apps mount this as `/data` |
| `/mnt/nas/downloads` | Download staging area on NAS (visible through mergerfs at `/mnt/media/downloads`) |

**mergerfs policy:** most-free-space — new writes automatically go to whichever drive has more room (currently the NAS). Downloads and library files both land on the NAS via this policy, enabling arr apps to hardlink on import (instant, zero-copy) instead of physically copying across filesystems.

**After this pipeline change, update root folders in each arr app's UI:**
- Radarr → Settings → Media Management → Root Folders: change `/movies` to `/data/movies`
- Sonarr → Settings → Media Management → Root Folders: change `/tv` to `/data/tv`
- Lidarr → Settings → Media Management → Root Folders: change `/music` to `/data/music`
- SABnzbd → Config → Folders: set Temporary Download Folder to `/data/downloads/incomplete`, Completed Download Folder to `/data/downloads/complete`
- qBittorrent → Settings → Downloads: set Default Save Path to `/data/downloads/complete`

**Key mergerfs tuning in fstab:** `cache.files=partial,dropcacheonclose=true` — buffers small writes through the kernel page cache before hitting the FUSE boundary. Critical for tag-writing performance (Lidarr, etc.) over NFS.

---

## Services

| Stack | What it does | Domain |
|-------|-------------|--------|
| Plex | Media server | plex.lan |
| Jellyfin | Media server (SSO via Authentik) | jellyfin.lan |
| Sonarr | TV show management | sonarr.lan |
| Radarr | Movie management | radarr.lan |
| Lidarr | Music management | lidarr.lan |
| Prowlarr | Indexer aggregator | prowlarr.lan |
| Bazarr | Subtitle management | bazarr.lan |
| Seer | Request management (Overseerr fork) | seer.lan |
| SABnzbd | Usenet downloader | sabnzbd.lan |
| qBittorrent | Torrent client (behind Gluetun VPN) | qbittorrent.lan |
| Gluetun | VPN kill-switch for qBittorrent | — |
| Unpackerr | Extracts completed downloads | — |
| Immich | Photo backup | immich.lan |
| Nextcloud | Personal cloud storage | nextcloud.lan |
| Vaultwarden | Password manager (Bitwarden-compatible) | vaultwarden.lan |
| Authentik | SSO / identity provider | auth.lan |
| Audiobookshelf | Audiobook + podcast server | audiobookshelf.lan |
| Calibre | Ebook library management | calibre-web.lan / calibre-content.lan |
| Bookshelf | Ebook + audiobook reader | bookshelf-ebooks.lan / bookshelf-audiobooks.lan |
| RomM | Game ROM library | romm.lan |
| Aurral | Music discovery | aurral.lan |
| AdGuard Home | DNS + ad blocking | adguard.lan |
| Caddy | Reverse proxy + TLS | — |
| Portainer | Docker management UI | portainer.lan |
| Homepage | Dashboard | homepage.lan |
| Uptime Kuma | Uptime monitoring | uptimekuma.lan |
| Glances | System stats | glances.lan |
| Netdata | System monitoring | netdata.lan |
| Scanopy | LAN device scanner | scanopy.lan |
| Pterodactyl | Minecraft server panel | pterodactyl.lan |

---

## What's in This Repo

```
mediahub-configs/
├── stacks/          # Docker Compose file for every Portainer stack
├── caddy/           # Caddyfile (all reverse proxy rules)
├── adguard/         # AdGuardHome.yaml (DNS rules, rewrites, blocklists)
├── system/          # fstab, docker daemon config, sudoers
├── systemd/         # Custom systemd units (wings cert reloader)
├── cron/            # Root crontab
└── README.md        # This file
```

**What's NOT here (intentionally):**
- `.env` — holds all secrets and machine-specific values (passwords, API keys, IPs). Copy `.env.example` to `.env` and fill it in. Never commit `.env` — it's gitignored.

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
# Storage mounts (edit fstab to match your drive UUIDs and NAS IP first)
sudo cp system/fstab /etc/fstab
sudo mkdir -p /mnt/internal /mnt/nas /mnt/media
sudo mount -a

# Firewall
sudo apt install -y ufw
# Apply rules from system/ufw-rules.txt

# Systemd units (Pterodactyl Wings cert reloader)
sudo cp systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wings-cert-reload.timer

# Cron
sudo crontab cron/root-crontab
```

---

## Updating This Repo

When you change a config file (edit a compose file in Portainer, update the Caddyfile, etc.), copy the updated file into this repo and commit:

```bash
cd ~/mediahub-configs

# Example: update a stack after editing it in Portainer
sudo cp /var/lib/docker/volumes/portainer_data/_data/compose/<id>/docker-compose.yml stacks/<name>/docker-compose.yml

git add .
git commit -m "describe what you changed"
git push
```

**Adding a new service checklist:**
- [ ] Add the service's docker-compose to `stacks/<name>/`
- [ ] Add a reverse proxy block to `caddy/Caddyfile` (`<name>.lan { tls internal; reverse_proxy ... }`)
- [ ] Add a DNS rewrite in AdGuard: `<name>.lan` → `100.104.43.6`
- [ ] Add an `extra_hosts` entry to `stacks/uptime-kuma/docker-compose.yml`: `- "<name>.lan:172.18.0.4"`
- [ ] Recreate Uptime Kuma to pick up the new hosts entry: `docker compose up -d --force-recreate uptime-kuma`
- [ ] Add a monitor in Uptime Kuma for `https://<name>.lan`

---

## Key IPs

Configure these in `.env` before running `setup.sh`:

| Variable | Description | Example |
|---|---|---|
| `SERVER_IP` | LAN IP of this machine | `192.168.0.21` |
| `TAILSCALE_IP` | Tailscale-assigned IP | `100.104.43.6` |
| `TAILSCALE_HOSTNAME` | Tailscale MagicDNS hostname | `mediahub-production.tail3b4ccf.ts.net` |
