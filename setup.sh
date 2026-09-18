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
command -v nvidia-smi >/dev/null || { echo "ERROR: nvidia-smi not found. Install the NVIDIA driver first (needed for Plex/Jellyfin hw transcode and docker-daemon.json's nvidia runtime)."; exit 1; }
command -v nvidia-ctk >/dev/null || { echo "ERROR: nvidia-container-toolkit not installed. Run: sudo apt install -y nvidia-container-toolkit"; exit 1; }
echo "✓ Prerequisites satisfied"

# ── Per-stack .env check ───────────────────────────────────────────────────────
# Each stack that needs secrets carries its own env file inside stacks/<name>/.
# There is no combined file at the repo root — that was the original design but
# it drifted after the 2026-08-23 Portainer migration and nothing here ever used
# it since. Two stacks use a nonstandard filename (their docker-compose.yml says
# so explicitly); everything else is a plain .env.
declare -A env_files=(
  [arr-stack]=.env
  [audiobookshelf]=.env
  [authentik]=.env
  [calibre]=.env
  [gluetun]=.env
  [immich]=.env
  [mealie]=mealie.env
  [nextcloud]=.env
  [obsidian-sync]=.env
  [plex]=.env
  [romm]=.env
  [rreading-glasses]=.env
  [scanopy]=.env
  [schedulesdirect-epg]=.env
  [threadfin]=.env
  [uptime-kuma]=.env
  [vaultwarden]=vaultwarden.env
  [wud]=.env
)
missing=()
for stack in "${!env_files[@]}"; do
  f="$REPO_DIR/stacks/$stack/${env_files[$stack]}"
  example="$REPO_DIR/stacks/$stack/.env.example"
  if [ ! -f "$f" ]; then
    if [ -f "$example" ]; then
      missing+=("stacks/$stack/${env_files[$stack]}  (copy from stacks/$stack/.env.example and fill in)")
    else
      missing+=("stacks/$stack/${env_files[$stack]}  (no .env.example present — check stacks/$stack/docker-compose.yml for what it needs)")
    fi
  fi
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: These per-stack env files are missing:"
  printf "  - %s\n" "${missing[@]}"
  echo ""
  echo "Fill each one in, then re-run: ./setup.sh"
  exit 1
fi
echo "✓ Per-stack env files present"

# ── Machine identity ───────────────────────────────────────────────────────────
# Auto-detected rather than stored in a file — the only genuinely cross-cutting
# value setup.sh needs, used solely to template AdGuard's DNS rewrites below.
SERVER_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") print $(i+1)}')"
[ -n "$SERVER_IP" ] || { echo "ERROR: could not auto-detect this machine's LAN IP. Check 'ip route get 1.1.1.1' and fix networking first."; exit 1; }
echo "✓ Detected SERVER_IP: $SERVER_IP"

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
  /srv/docker/iptvorg-epg
  /srv/docker/jellyfin
  /srv/docker/mealie/pgdata
  /srv/docker/mealie/data
  /srv/docker/nextcloud
  /srv/docker/opengym/data
  /srv/docker/plex
  /srv/docker/qbittorrent
  /srv/docker/romm/assets
  /srv/docker/romm/config
  /srv/docker/rreading-glasses
  /srv/docker/sabnzbd
  /srv/docker/seer
  /srv/docker/stirling-pdf/config
  /srv/docker/stirling-pdf/logs
  /srv/docker/threadfin/conf/iptvorg-epg
  /srv/docker/vaultwarden
  /srv/docker/visibility/netdata
  /srv/docker/visibility/wud
  /mnt/internal
  /mnt/nas
  /mnt/media
)
for dir in "${dirs[@]}"; do
  sudo mkdir -p "$dir"
done
# Authentik runs as a non-root user internally and needs to create subdirs here
sudo chmod -R 777 /srv/docker/authentik/media /srv/docker/authentik/certs
# seer (bundled in arr-stack) runs as PUID=1000/PGID=1000 and writes logs here —
# leaving this root-owned crash-loops it with EACCES on startup
sudo chown -R 1000:1000 /srv/docker/seer
echo "✓ Directory structure created"

# ── openGym source ─────────────────────────────────────────────────────────────
# openGym's app code is a third-party upstream project, not part of this repo —
# its docker-compose.yml builds images directly from a checkout at this path.
if [ -d /srv/docker/opengym/src/.git ]; then
  (cd /srv/docker/opengym/src && sudo git pull --ff-only)
else
  sudo rm -rf /srv/docker/opengym/src
  sudo git clone https://github.com/arvids-unavailable/openGym.git /srv/docker/opengym/src
fi
echo "✓ openGym source ready"

# ── iptvorg-epg channel list ───────────────────────────────────────────────────
# Single-file bind mount — if the host path doesn't exist as a file BEFORE the
# container first starts, Docker silently creates it as an empty directory
# instead and the container fails to mount at all. Same trap as the Caddyfile
# below. See docs/fresh-machine-bring-up.md.
sudo cp "$REPO_DIR/stacks/iptvorg-epg/channels.xml" /srv/docker/iptvorg-epg/channels.xml
echo "✓ iptvorg-epg channel list staged"

# ── Stirling PDF OCR languages ─────────────────────────────────────────────────
# Bind-mounting tessdata masks the image's built-in language packs, so seed the
# host dir from the image first via a disposable container. Adds Spanish, which
# isn't in the base image. See docs/fresh-machine-bring-up.md.
if [ -z "$(sudo ls -A /srv/docker/stirling-pdf/tessdata 2>/dev/null)" ]; then
  sudo mkdir -p /srv/docker/stirling-pdf/tessdata
  docker run --rm --entrypoint sh -v /srv/docker/stirling-pdf/tessdata:/dest \
    stirlingtools/stirling-pdf:2.14.3-fat \
    -c "cp -r /usr/share/tesseract-ocr/5/tessdata/. /dest/"
  sudo curl -fsSL -o /srv/docker/stirling-pdf/tessdata/spa.traineddata \
    https://github.com/tesseract-ocr/tessdata/raw/main/spa.traineddata
  echo "✓ Stirling PDF tessdata seeded (eng/deu/fra/por/chi_sim/osd/spa)"
else
  echo "✓ Stirling PDF tessdata already seeded"
fi

# ── romm's legacy Portainer-named volumes ──────────────────────────────────────
# romm's docker-compose.yml declares these as external. They only exist on
# production because Portainer auto-created them years ago; nothing here
# creates them, so `docker compose up` for romm fails outright on a fresh
# machine unless they're created first.
for vol in 33_mysql-data 33_romm-redis-data 33_romm-resources; do
  docker volume inspect "$vol" >/dev/null 2>&1 || docker volume create "$vol"
done
echo "✓ romm's legacy volumes ready"

# ── Docker network ─────────────────────────────────────────────────────────────
docker network inspect mediahub_internal >/dev/null 2>&1 || \
  docker network create --driver bridge --subnet 172.18.0.0/16 mediahub_internal
echo "✓ Docker network ready (mediahub_internal @ 172.18.0.0/16)"

# ── Caddyfile ───────────────────────────────────────────────────────────────────
# Checked into the repo with the real config already, no placeholder to
# substitute — copy as-is. Single-file bind mount, same directory-auto-create
# trap as above: must exist as a real file before caddy's container first starts.
sudo cp "$REPO_DIR/caddy/Caddyfile" /srv/docker/caddy/Caddyfile
echo "✓ Caddyfile written to /srv/docker/caddy/Caddyfile"

# ── Process AdGuard template ───────────────────────────────────────────────────
sed "s/SERVER_IP_PLACEHOLDER/${SERVER_IP}/g" \
  "$REPO_DIR/adguard/AdGuardHome.yaml" | sudo tee /srv/docker/adguardhome/conf/AdGuardHome.yaml >/dev/null
echo "✓ AdGuardHome.yaml written to /srv/docker/adguardhome/conf/"

# ── Docker daemon config ───────────────────────────────────────────────────────
if [ -f "$REPO_DIR/system/docker-daemon.json" ]; then
  sudo cp "$REPO_DIR/system/docker-daemon.json" /etc/docker/daemon.json
  sudo systemctl restart docker
  echo "  Waiting for Docker daemon..."
  until docker info >/dev/null 2>&1; do sleep 1; done
  echo "✓ Docker daemon config applied (MTU + NVIDIA runtime)"
fi

# ── Start stacks in dependency order ──────────────────────────────────────────
# adguard first (DNS), caddy second (TLS), authentik third (several stacks do
# OIDC against it), then everything else.
stacks=(
  adguard
  caddy
  authentik
  immich
  nextcloud
  mealie
  romm
  vaultwarden
  jellyfin
  plex
  sabnzbd
  gluetun
  arr-stack
  audiobookshelf
  calibre
  aurral
  scanopy
  rreading-glasses
  opengym
  iptvorg-epg
  threadfin
  stirling-pdf
  uptime-kuma
  wud
)

echo ""
echo "Starting stacks..."
for stack in "${stacks[@]}"; do
  compose_file="$REPO_DIR/stacks/$stack/docker-compose.yml"
  if [ -f "$compose_file" ]; then
    echo "  ▶ $stack"
    (cd "$REPO_DIR/stacks/$stack" && docker compose up -d 2>&1 | grep -v "^time=") || true

    # After Caddy starts: wait for its CA cert, then distribute it everywhere
    # it's needed — the system trust store, Jellyfin, and the extracted copy
    # that nextcloud/immich/mealie/audiobookshelf bind-mount for
    # REQUESTS_CA_BUNDLE / OIDC TLS verification. All of these are single-file
    # binds, so this must happen before any of those stacks start.
    if [ "$stack" = "caddy" ]; then
      caddy_cert="/srv/docker/caddy/data/caddy/pki/authorities/local/root.crt"
      echo "    Waiting for Caddy CA cert..."
      until sudo test -f "$caddy_cert"; do sleep 2; done
      sudo cp "$caddy_cert" /usr/local/share/ca-certificates/caddy-root.crt
      sudo chmod 644 /usr/local/share/ca-certificates/caddy-root.crt
      sudo update-ca-certificates >/dev/null 2>&1
      sudo mkdir -p /srv/docker/jellyfin/config
      sudo cp "$caddy_cert" /srv/docker/jellyfin/config/caddy-ca.crt
      sudo cp "$caddy_cert" /srv/docker/caddy/caddy-root.crt
      sudo chmod 644 /srv/docker/jellyfin/config/caddy-ca.crt /srv/docker/caddy/caddy-root.crt
      echo "✓ Caddy CA cert distributed (system trust store, Jellyfin, and /srv/docker/caddy/caddy-root.crt)"
    fi
  else
    echo "  ⚠ Skipping $stack (no compose file at stacks/$stack/docker-compose.yml)"
  fi
done

echo ""
echo "✅ mediahub is up!"
echo ""
echo "━━━ Next steps ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. Point your router's DNS server to: ${SERVER_IP}"
echo ""
echo "2. In Tailscale admin console:"
echo "   - Approve subnet route: 192.168.0.0/24"
echo "   - Set Split DNS: .lan → ${SERVER_IP}"
echo ""
echo "3. Visit https://adguard.lan and set your admin password"
echo ""
echo "4. See docs/fresh-machine-bring-up.md for what's still manual after this:"
echo "   UFW rules, cron jobs, storage mount UUIDs, and anything setup.sh"
echo "   can't safely automate (like which .env values to actually put where)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
