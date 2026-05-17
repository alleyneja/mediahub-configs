# Portable mediahub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform mediahub-configs into a cloneable, self-installing repository so anyone can `git clone` + `./setup.sh` and get a working media server on a fresh machine.

**Architecture:** Introduce a `.env` file for all secrets and machine-specific values; replace hardcoded IPs and tokens in config files and compose files with environment variable references; write a `setup.sh` bootstrap script that creates directory structure, processes config templates, creates the Docker network, and starts all stacks in dependency order. Validate the full flow on the 192.168.0.20 test machine.

**Tech Stack:** Bash, Docker Compose (native `.env` expansion), `sed` (template substitution for Caddyfile and AdGuardHome.yaml which don't natively support env vars)

---

## File Map

**Created:**
- `.env.example` — lists every required variable with descriptions and generation commands
- `setup.sh` — bootstrap script: validates env, creates dirs, processes templates, starts stacks

**Modified:**
- `caddy/Caddyfile` — replace 2 hardcoded `192.168.0.21` occurrences with `SERVER_IP_PLACEHOLDER`
- `adguard/AdGuardHome.yaml` — replace all `100.104.43.6` with `SERVER_IP_PLACEHOLDER`, strip personal client entries, clear password hash
- `stacks/plex/docker-compose.yml` — replace hardcoded `PLEX_CLAIM=claim-...` with `${PLEX_CLAIM}`
- `stacks/uptime-kuma/docker-compose.yml` — parameterize `dns:` IP, Tailscale extra_hosts entry, and `HOMEPAGE_ALLOWED_HOSTS`
- `stacks/audiobookshelf/docker-compose.yml` — parameterize `dns:` entry
- `stacks/immich/docker-compose.yml` — parameterize `dns:` entry
- `stacks/nextcloud/docker-compose.yml` — parameterize `dns:` entry
- `stacks/calibre/docker-compose.yml` — parameterize `dns:` entry
- `README.md` — replace manual rebuild steps with `./setup.sh` instructions

**Note on two placeholder styles:**
- Compose files use `${VAR}` — Docker Compose expands these natively from `.env`
- Caddyfile and AdGuardHome.yaml use the literal string `SERVER_IP_PLACEHOLDER` — `setup.sh` uses `sed` to substitute before writing to `/srv/docker/`

---

## Tasks

### Task 1: Create `.env.example`

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Create the file**

```bash
cat > /home/jay/mediahub-configs/.env.example << 'EOF'
# ─────────────────────────────────────────
# SERVER IDENTITY
# ─────────────────────────────────────────

# LAN IP of this machine (run: ip addr show | grep "inet " to find it)
SERVER_IP=

# Tailscale IP assigned to this machine (run: tailscale ip -4)
TAILSCALE_IP=

# Tailscale MagicDNS hostname (find in Tailscale admin console under this machine's entry)
# Example: mediahub-production.tail3b4ccf.ts.net
TAILSCALE_HOSTNAME=

# ─────────────────────────────────────────
# PLEX
# ─────────────────────────────────────────

# One-time claim token from https://plex.tv/claim
# Valid for only 4 minutes — grab this right before running setup.sh
PLEX_CLAIM=

# ─────────────────────────────────────────
# VPN (NordVPN / Gluetun)
# ─────────────────────────────────────────

# NordVPN access token — generate at https://my.nordaccount.com → Security → Access Tokens
NORDVPN_ACCESS_TOKEN=

# WireGuard private key — generate via NordVPN API after getting the access token
# See: https://gluetun.wiki/setup/providers/nordvpn/#wireguard
WIREGUARD_PRIVATE_KEY=

# ─────────────────────────────────────────
# IMMICH
# ─────────────────────────────────────────

# Strong random password for Immich Postgres
# Generate: openssl rand -hex 32
IMMICH_DB_PASSWORD=

# ─────────────────────────────────────────
# AUTHENTIK
# ─────────────────────────────────────────

# Strong random password for Authentik Postgres
# Generate: openssl rand -hex 32
AUTHENTIK_POSTGRES_PASSWORD=

# Long random secret key for Authentik (50+ chars)
# Generate: openssl rand -hex 50
AUTHENTIK_SECRET_KEY=

# ─────────────────────────────────────────
# ROMM (Game Library)
# ─────────────────────────────────────────

# Password for RomM's MariaDB database
# Generate: openssl rand -hex 32
ROMM_DB_PASSWORD=

# MariaDB root password
# Generate: openssl rand -hex 32
ROMM_MYSQL_ROOT_PASSWORD=

# Random secret key for RomM auth
# Generate: openssl rand -hex 32
ROMM_AUTH_SECRET_KEY=

# IGDB API credentials for game metadata
# Register at: https://api-docs.igdb.com/#getting-started
IGDB_CLIENT_ID=
IGDB_CLIENT_SECRET=
EOF
```

- [ ] **Step 2: Verify `.env` is already gitignored**

```bash
grep "^\.env" /home/jay/mediahub-configs/.gitignore
# Expected: .env
```

- [ ] **Step 3: Commit**

```bash
cd /home/jay/mediahub-configs
git add .env.example
git commit -m "feat: add .env.example with all required variables"
```

---

### Task 2: Parameterize Caddyfile

**Files:**
- Modify: `caddy/Caddyfile` (2 lines: adguard.lan proxy and pterodactyl.lan:8090 proxy)

These two entries point to `192.168.0.21` because AdGuard and Pterodactyl use host networking and can't be reached by container name. They must stay as IPs — we just make the IP dynamic.

- [ ] **Step 1: Replace the two hardcoded IPs**

```bash
sed -i 's/192\.168\.0\.21/SERVER_IP_PLACEHOLDER/g' /home/jay/mediahub-configs/caddy/Caddyfile
```

- [ ] **Step 2: Verify exactly 2 substitutions, no bare IPs remain**

```bash
grep "192.168.0" /home/jay/mediahub-configs/caddy/Caddyfile
# Expected: no output

grep "SERVER_IP_PLACEHOLDER" /home/jay/mediahub-configs/caddy/Caddyfile
# Expected: exactly 2 lines (adguard.lan and pterodactyl.lan:8090)
```

- [ ] **Step 3: Commit**

```bash
cd /home/jay/mediahub-configs
git add caddy/Caddyfile
git commit -m "feat: replace hardcoded IPs in Caddyfile with SERVER_IP_PLACEHOLDER"
```

---

### Task 3: Parameterize AdGuardHome.yaml

**Files:**
- Modify: `adguard/AdGuardHome.yaml`

Three changes: replace all rewrite IPs, clear the password hash, strip personal client entries.

- [ ] **Step 1: Replace all DNS rewrite answer IPs**

```bash
sed -i 's/answer: 100\.104\.43\.6/answer: SERVER_IP_PLACEHOLDER/g' \
  /home/jay/mediahub-configs/adguard/AdGuardHome.yaml
```

Verify:
```bash
grep "100.104.43.6" /home/jay/mediahub-configs/adguard/AdGuardHome.yaml
# Expected: no output

grep "SERVER_IP_PLACEHOLDER" /home/jay/mediahub-configs/adguard/AdGuardHome.yaml | wc -l
# Expected: 30
```

- [ ] **Step 2: Clear the admin password hash**

Open `adguard/AdGuardHome.yaml` and find the users block (around line 7):
```yaml
users:
  - name: admin
    password: $2a$10$TU94lCtVrIVED8L5jn8GpOfRn0t/pHepA2ZV3KC.qGdiAXMJe5sKG
```

Replace with:
```yaml
users: []
```

(AdGuard will prompt for a new password on first boot when the users list is empty.)

- [ ] **Step 3: Strip personal client entries**

Find the `clients:` section (around line 357). Replace the entire `persistent:` list with an empty list. The `clients:` block should look like this after editing:

```yaml
clients:
  runtime_sources:
    whois: true
    arp: true
    rdns: true
    dhcp: true
    hosts: true
  persistent: []
```

- [ ] **Step 4: Validate the file is still valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/jay/mediahub-configs/adguard/AdGuardHome.yaml'))" && echo "Valid YAML"
# Expected: Valid YAML
```

- [ ] **Step 5: Commit**

```bash
cd /home/jay/mediahub-configs
git add adguard/AdGuardHome.yaml
git commit -m "feat: parameterize AdGuard YAML — placeholder IPs, empty clients, cleared password"
```

---

### Task 4: Parameterize compose files

**Files:**
- Modify: `stacks/plex/docker-compose.yml`
- Modify: `stacks/uptime-kuma/docker-compose.yml`
- Modify: `stacks/audiobookshelf/docker-compose.yml`
- Modify: `stacks/immich/docker-compose.yml`
- Modify: `stacks/nextcloud/docker-compose.yml`
- Modify: `stacks/calibre/docker-compose.yml`

Docker Compose natively expands `${VAR}` from the `.env` file, so these use the standard syntax — no sed needed.

- [ ] **Step 1: Plex — parameterize claim token**

In `stacks/plex/docker-compose.yml`, change:
```yaml
      - PLEX_CLAIM=claim-CKx96EC3gh2MLQksSqas
```
To:
```yaml
      - PLEX_CLAIM=${PLEX_CLAIM}
```

- [ ] **Step 2: Uptime Kuma — parameterize all three hardcoded values**

In `stacks/uptime-kuma/docker-compose.yml`:

Change the `dns:` entry:
```yaml
    dns:
      - 192.168.0.21
```
To:
```yaml
    dns:
      - ${SERVER_IP}
```

Change the Tailscale `extra_hosts` entry:
```yaml
      - "mediahub-production.tail3b4ccf.ts.net:100.104.43.6"
```
To:
```yaml
      - "${TAILSCALE_HOSTNAME}:${TAILSCALE_IP}"
```

Change `HOMEPAGE_ALLOWED_HOSTS`:
```yaml
      - HOMEPAGE_ALLOWED_HOSTS=192.168.0.21:3080,homepage.lan,mediahub-production.tail3b4ccf.ts.net:3080
```
To:
```yaml
      - HOMEPAGE_ALLOWED_HOSTS=${SERVER_IP}:3080,homepage.lan,${TAILSCALE_HOSTNAME}:3080
```

- [ ] **Step 3: Audiobookshelf — parameterize dns entry**

In `stacks/audiobookshelf/docker-compose.yml`, change:
```yaml
    dns:
      - 192.168.0.21
```
To:
```yaml
    dns:
      - ${SERVER_IP}
```

- [ ] **Step 4: Immich — parameterize dns entry**

In `stacks/immich/docker-compose.yml`, change:
```yaml
    dns:
      - 192.168.0.21
```
To:
```yaml
    dns:
      - ${SERVER_IP}
```

- [ ] **Step 5: Nextcloud — parameterize dns entry**

In `stacks/nextcloud/docker-compose.yml`, change:
```yaml
    dns:
      - 192.168.0.21
```
To:
```yaml
    dns:
      - ${SERVER_IP}
```

- [ ] **Step 6: Calibre — parameterize dns entry**

In `stacks/calibre/docker-compose.yml`, change:
```yaml
    dns:
      - 192.168.0.21
```
To:
```yaml
    dns:
      - ${SERVER_IP}
```

- [ ] **Step 7: Verify no bare IPs or hardcoded tokens remain across all stacks**

```bash
grep -rn "192\.168\.0\.21\|100\.104\.43\.6\|tail3b4ccf\|claim-" \
  /home/jay/mediahub-configs/stacks/
# Expected: no output
```

- [ ] **Step 8: Commit**

```bash
cd /home/jay/mediahub-configs
git add stacks/plex/docker-compose.yml \
        stacks/uptime-kuma/docker-compose.yml \
        stacks/audiobookshelf/docker-compose.yml \
        stacks/immich/docker-compose.yml \
        stacks/nextcloud/docker-compose.yml \
        stacks/calibre/docker-compose.yml
git commit -m "feat: parameterize hardcoded IPs and tokens across all compose files"
```

---

### Task 5: Write `setup.sh`

**Files:**
- Create: `setup.sh`

- [ ] **Step 1: Write the bootstrap script**

```bash
cat > /home/jay/mediahub-configs/setup.sh << 'SETUP'
#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== mediahub setup ==="
echo ""

# ── Prerequisites ──────────────────────────────────────────────────────────────
for cmd in docker git curl python3; do
  command -v "$cmd" >/dev/null || { echo "ERROR: $cmd is not installed. See README for install instructions."; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose plugin not found. Install Docker Engine (not docker.io)."; exit 1; }
echo "✓ Prerequisites satisfied"

# ── .env bootstrap ─────────────────────────────────────────────────────────────
if [ ! -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  echo ""
  echo "  .env created from .env.example."
  echo "  Open it and fill in all values, then re-run: ./setup.sh"
  echo ""
  exit 0
fi

# shellcheck source=/dev/null
set -a; source "$REPO_DIR/.env"; set +a

required_vars=(
  SERVER_IP TAILSCALE_IP TAILSCALE_HOSTNAME PLEX_CLAIM
  NORDVPN_ACCESS_TOKEN WIREGUARD_PRIVATE_KEY
  IMMICH_DB_PASSWORD
  AUTHENTIK_POSTGRES_PASSWORD AUTHENTIK_SECRET_KEY
  ROMM_DB_PASSWORD ROMM_MYSQL_ROOT_PASSWORD ROMM_AUTH_SECRET_KEY
  IGDB_CLIENT_ID IGDB_CLIENT_SECRET
)
missing=()
for var in "${required_vars[@]}"; do
  [ -z "${!var:-}" ] && missing+=("$var")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: These variables are not set in .env:"
  printf "  - %s\n" "${missing[@]}"
  exit 1
fi
echo "✓ .env validated"

# ── Directory structure ────────────────────────────────────────────────────────
dirs=(
  /srv/docker/adguardhome/work
  /srv/docker/adguardhome/conf
  /srv/docker/audiobookshelf
  /srv/docker/aurral
  /srv/docker/authentik/postgres
  /srv/docker/authentik/redis
  /srv/docker/authentik/media
  /srv/docker/authentik/certs
  /srv/docker/bookshelf/audiobooks
  /srv/docker/bookshelf/ebooks
  /srv/docker/caddy/data
  /srv/docker/caddy/config
  /srv/docker/calibre-content-server
  /srv/docker/calibre-web
  /srv/docker/gluetun
  /srv/docker/homepage
  /srv/docker/immich/redis
  /srv/docker/immich/postgres
  /srv/docker/jellyfin
  /srv/docker/nextcloud
  /srv/docker/plex
  /srv/docker/qbittorrent
  /srv/docker/romm
  /srv/docker/rreading-glasses
  /srv/docker/sabnzbd
  /srv/docker/vaultwarden
  /srv/docker/visibility/netdata
  /mnt/internal
  /mnt/nas
  /mnt/media
)
for dir in "${dirs[@]}"; do
  sudo mkdir -p "$dir"
done
echo "✓ Directory structure created"

# ── Docker network ─────────────────────────────────────────────────────────────
docker network inspect mediahub_internal >/dev/null 2>&1 || \
  docker network create --driver bridge --subnet 172.18.0.0/16 mediahub_internal
echo "✓ Docker network ready (mediahub_internal @ 172.18.0.0/16)"

# ── Process Caddyfile template ─────────────────────────────────────────────────
sed "s/SERVER_IP_PLACEHOLDER/${SERVER_IP}/g" \
  "$REPO_DIR/caddy/Caddyfile" > /srv/docker/caddy/Caddyfile
echo "✓ Caddyfile written to /srv/docker/caddy/Caddyfile"

# ── Process AdGuard template ───────────────────────────────────────────────────
sed "s/SERVER_IP_PLACEHOLDER/${SERVER_IP}/g" \
  "$REPO_DIR/adguard/AdGuardHome.yaml" > /srv/docker/adguardhome/conf/AdGuardHome.yaml
echo "✓ AdGuardHome.yaml written to /srv/docker/adguardhome/conf/"

# ── Docker daemon config ───────────────────────────────────────────────────────
if [ -f "$REPO_DIR/system/docker-daemon.json" ]; then
  sudo cp "$REPO_DIR/system/docker-daemon.json" /etc/docker/daemon.json
  sudo systemctl restart docker
  echo "✓ Docker daemon config applied (MTU + NVIDIA runtime)"
fi

# ── Start stacks in dependency order ──────────────────────────────────────────
# adguard first (DNS), caddy second (TLS), then everything else
stacks=(
  adguard
  caddy
  authentik
  immich
  nextcloud
  vaultwarden
  jellyfin
  plex
  sabnzbd
  gluetun
  arr-stack
  audiobookshelf
  calibre
  romm
  aurral
  scanopy
  rreading-glasses
  uptime-kuma
)

echo ""
echo "Starting stacks..."
for stack in "${stacks[@]}"; do
  compose_file="$REPO_DIR/stacks/$stack/docker-compose.yml"
  if [ -f "$compose_file" ]; then
    echo "  ▶ $stack"
    docker compose --env-file "$REPO_DIR/.env" -f "$compose_file" up -d 2>&1 | grep -v "^time=" || true
  else
    echo "  ⚠ Skipping $stack (no compose file at stacks/$stack/docker-compose.yml)"
  fi
done

echo ""
echo "✅ mediahub is up!"
echo ""
echo "━━━ Next steps ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Install Caddy's CA cert so browsers trust .lan domains:"
echo "   sudo cp /srv/docker/caddy/data/caddy/pki/authorities/local/root.crt \\"
echo "           /usr/local/share/ca-certificates/caddy-root.crt"
echo "   sudo update-ca-certificates"
echo ""
echo "2. Point your router's DNS server to: ${SERVER_IP}"
echo ""
echo "3. In Tailscale admin console:"
echo "   - Approve subnet route: 192.168.0.0/24"
echo "   - Set Split DNS: .lan → ${SERVER_IP}"
echo ""
echo "4. Visit https://adguard.lan and set your admin password"
echo "5. Visit https://portainer.lan to manage stacks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
SETUP
```

- [ ] **Step 2: Make executable and verify it parses without errors**

```bash
chmod +x /home/jay/mediahub-configs/setup.sh
bash -n /home/jay/mediahub-configs/setup.sh && echo "Syntax OK"
# Expected: Syntax OK
```

- [ ] **Step 3: Commit**

```bash
cd /home/jay/mediahub-configs
git add setup.sh
git commit -m "feat: add setup.sh bootstrap script for fresh installs"
```

---

### Task 6: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the "## Rebuilding from Scratch" section**

Find the `## Rebuilding from Scratch` heading and replace the entire section (everything through the end of the numbered steps) with:

```markdown
## Fresh Install

Clone this repo and run the setup script:

```bash
git clone https://github.com/alleyneja/mediahub-configs.git
cd mediahub-configs
./setup.sh
```

The first run creates `.env` from `.env.example` and exits. Fill in all values, then run `./setup.sh` again — it handles everything: directory structure, Docker network, config template processing, and starting all stacks.

### Prerequisites (install before running setup.sh)

```bash
# Docker Engine (not docker.io — the Engine package includes the Compose plugin)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --advertise-routes=192.168.0.0/24 --accept-dns=false
# Then approve the subnet route in the Tailscale admin console
```

### After setup.sh completes

1. **Install Caddy's CA cert** so browsers trust `.lan` domains:
   ```bash
   sudo cp /srv/docker/caddy/data/caddy/pki/authorities/local/root.crt \
           /usr/local/share/ca-certificates/caddy-root.crt
   sudo update-ca-certificates
   ```
2. **Point your router's DNS** at `SERVER_IP`
3. **Tailscale admin console:** approve the subnet route and set Split DNS for `.lan` → `SERVER_IP`
4. Visit `https://adguard.lan` — set your admin password (the config ships with no password)
5. Visit `https://portainer.lan` to manage and monitor all stacks

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
```

- [ ] **Step 2: Replace the hardcoded Key IPs table**

Find the `## Key IPs` section and replace with:

```markdown
## Key IPs

Configure these in `.env` before running `setup.sh`:

| Variable | Description | Example |
|---|---|---|
| `SERVER_IP` | LAN IP of this machine | `192.168.0.21` |
| `TAILSCALE_IP` | Tailscale-assigned IP | `100.104.43.6` |
| `TAILSCALE_HOSTNAME` | Tailscale MagicDNS hostname | `mediahub-production.tail3b4ccf.ts.net` |
```

- [ ] **Step 3: Commit**

```bash
cd /home/jay/mediahub-configs
git add README.md
git commit -m "docs: update README with fresh install via setup.sh, remove manual rebuild steps"
```

---

### Task 7: Test run on 192.168.0.20

Validate that the whole thing works end-to-end on a genuinely fresh machine. Every failure becomes a setup.sh fix.

- [ ] **Step 1: Wipe 192.168.0.20 and install Ubuntu Server 24.04**

Boot from USB installer. Create user `jay`. No extras needed beyond base install.

- [ ] **Step 2: Install prerequisites on the fresh machine**

```bash
# SSH in from mediahub-production:
ssh jay@192.168.0.20

# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker jay
newgrp docker

# Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --accept-dns=false
# Note the Tailscale IP assigned — you'll need it for .env
```

- [ ] **Step 3: Clone and first run**

```bash
git clone https://github.com/alleyneja/mediahub-configs.git
cd mediahub-configs
./setup.sh
# Expected output ends with:
#   .env created from .env.example.
#   Open it and fill in all values, then re-run: ./setup.sh
```

- [ ] **Step 4: Fill in .env and run again**

```bash
nano .env
# Fill in SERVER_IP (192.168.0.20 for the test machine)
# Fill in TAILSCALE_IP and TAILSCALE_HOSTNAME from `tailscale status`
# Fill in all secrets (can reuse prod values for testing)
# PLEX_CLAIM: get a fresh one at https://plex.tv/claim right before running

./setup.sh
# Expected: stacks start one by one, ends with "✅ mediahub is up!"
```

- [ ] **Step 5: Verify no containers are in a bad state**

```bash
docker ps --format "{{.Names}}: {{.Status}}" | grep -v "Up"
# Expected: no output (all containers Up)

docker ps --format "{{.Names}}: {{.Status}}" | grep "unhealthy\|Exit\|Restarting"
# Expected: no output
```

- [ ] **Step 6: Verify Caddy and AdGuard are serving**

```bash
# Check AdGuard web UI is responding
curl -s -o /dev/null -w "%{http_code}" http://192.168.0.20:3000
# Expected: 200 or 302

# Check Caddy is listening on 443
curl -sk --resolve "plex.lan:443:192.168.0.20" https://plex.lan -o /dev/null -w "%{http_code}"
# Expected: 200 or 401 (anything other than connection refused)
```

- [ ] **Step 7: Fix and commit any failures**

Each failure = a bug in setup.sh or a missed parameterization. Fix it, push, and re-test from a clean `.env` (don't re-wipe — just `docker rm -f $(docker ps -aq)` and re-run `./setup.sh`).

```bash
# On mediahub-production, after fixing:
cd /home/jay/mediahub-configs
git add -A
git commit -m "fix: <describe what broke during test run>"
git push

# On test machine:
git pull
./setup.sh
```
