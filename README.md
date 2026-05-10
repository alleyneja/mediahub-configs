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
| `/mnt/internal` | 12TB internal HDD (ext4) |
| `/mnt/nas` | UGREEN NAS share over NFS (Btrfs, 22TB free) |
| `/mnt/media` | mergerfs pool combining both — all Docker apps point here |
| `/mnt/downloads` | Download staging area on internal HDD |

**mergerfs policy:** most-free-space — new writes automatically go to whichever drive has more room (currently the NAS).

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
- `stack.env` files — these hold real passwords and stay on the server only. Each stack that needs secrets has a `stack.env` file in its Portainer compose directory at `/var/lib/docker/volumes/portainer_data/_data/compose/<id>/stack.env`.

---

## Rebuilding from Scratch

If mediahub ever needs to be rebuilt on a new machine, do it in this order:

### 1. Fresh Ubuntu install
Install Ubuntu Server 24.04 on the machine. Create user `jay`.

### 2. Basic system setup
```bash
# Passwordless sudo
echo "jay ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/jay-nopasswd

# Install dependencies
sudo apt update && sudo apt install -y nfs-common mergerfs docker.io docker-compose git

# Docker daemon (sets MTU to 1280 for Tailscale compatibility, adds NVIDIA runtime)
sudo cp system/docker-daemon.json /etc/docker/daemon.json
sudo systemctl restart docker
```

### 3. Storage
```bash
# Mount internal HDD and NAS
sudo cp system/fstab /etc/fstab
sudo mkdir -p /mnt/internal /mnt/nas /mnt/media /mnt/downloads
sudo mount -a
```

### 4. Tailscale
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --advertise-routes=192.168.0.0/24 --accept-dns=false
# Then approve the subnet route in the Tailscale admin console
```

### 5. Create shared Docker network and configure firewall
```bash
docker network create mediahub_internal
```

Install and configure UFW:
```bash
sudo apt install -y ufw

# Allow loopback
sudo ufw allow in on lo

# Allow all traffic from LAN
sudo ufw allow from 192.168.0.0/24 comment "LAN"

# Allow Tailscale CGNAT range and interface
sudo ufw allow from 100.64.0.0/10 comment "Tailscale CGNAT"
sudo ufw allow in on tailscale0 comment "Tailscale interface"
sudo ufw allow 41641/udp comment "Tailscale VPN port"

# Allow Docker bridge networks to reach host services
# (required for containers to reverse-proxy to host-networked services like AdGuard)
sudo ufw allow from 172.16.0.0/12 comment "Docker bridge networks"

sudo ufw enable
```

### 6. Caddy (must come first — everything else needs TLS)
```bash
mkdir -p /srv/docker/caddy
cp caddy/Caddyfile /srv/docker/caddy/Caddyfile
# Deploy the caddy stack via Portainer, then install the Caddy local CA:
sudo cp /srv/docker/caddy/caddy-root.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

### 7. AdGuard Home
```bash
# Deploy the adguard stack via Portainer
# Restore config:
sudo cp adguard/AdGuardHome.yaml /srv/docker/adguardhome/conf/AdGuardHome.yaml
docker restart adguardhome
# Then point your router's DNS to this machine's IP
```

### 8. Portainer
Deploy the portainer stack. Then import all stacks from `stacks/` via Portainer UI. For each stack that has secrets, create a `stack.env` file in its Portainer directory with the real values before deploying.

### 9. Authentik
Deploy authentik stack. Re-create the Jellyfin OIDC provider (client_id and provider settings are stored in Authentik's database, not in config files).

### 10. Systemd units
```bash
sudo cp systemd/wings-cert-reload.timer /etc/systemd/system/
sudo cp systemd/wings-cert-reload.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wings-cert-reload.timer
```

### 11. Cron jobs
```bash
sudo crontab cron/root-crontab
```
The crontab includes:
- **Sun + Wed at 3AM:** `apt update && apt upgrade -y && reboot` — keeps the system patched and clears Docker memory overhead that accumulates over days of uptime
- **Daily at 3AM:** Renews Pterodactyl's Wings TLS cert ownership and restarts Wings
- **Every minute:** Pterodactyl scheduler

### 12. Pterodactyl
Pterodactyl (the game server panel) is installed directly on the host (not in Docker) and uses PHP + Caddy. Its config lives in `/var/www/pterodactyl`. This is not backed up here — refer to the official Pterodactyl docs for reinstallation.

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

---

## Key IPs

| Device | LAN IP | Tailscale IP |
|--------|--------|-------------|
| mediahub-production | 192.168.0.21 | 100.104.43.6 |
| UGREEN NAS | 192.168.0.23 | — |
