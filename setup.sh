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
  NEXTCLOUD_ADMIN_PASSWORD NEXTCLOUD_POSTGRES_PASSWORD
  POSTGRES_PASSWORD RG_POSTGRES_PASSWORD
  UN_SONARR_0_API_KEY UN_RADARR_0_API_KEY UN_LIDARR_0_API_KEY
  UN_READARR_0_API_KEY UN_READARR_1_API_KEY
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
  "$REPO_DIR/caddy/Caddyfile" | sudo tee /srv/docker/caddy/Caddyfile >/dev/null
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
echo "━━━ Next steps ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
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
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
