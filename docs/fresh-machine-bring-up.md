# Bringing up mediahub on a fresh machine

Written after a full rehearsal on `mediahub-staging` (Alienware X51 R2) on
2026-09-17/18: full wipe (Docker purged, `/srv/docker` deleted, repo re-cloned
from scratch) and rebuilt from a bare Ubuntu 24.04 box using nothing but this
repo and production's `.env` files. This is what that rehearsal found — read
it before the next real machine bring-up (the PCBS2 replacement rig).

`setup.sh` has been updated to automate everything below that's safe to
automate. What's left in this doc is either a one-time manual step, or
context for *why* setup.sh does what it does — useful when it breaks.

## Order matters, and here's why

1. **`adguard`** (DNS) — nothing strictly depends on it starting first, but
   every other `.lan` hostname resolves through it.
2. **`caddy`** (reverse proxy / internal TLS CA) — generates a **brand new**
   internal CA on first boot. Everything that needs to trust `*.lan` TLS
   certs (Jellyfin, Nextcloud, Immich, Mealie, audiobookshelf, romm) needs a
   copy of *this* CA's root cert, not a copy from another machine's Caddy.
3. **`authentik`** — several stacks do OIDC against it.
4. Everything else.

`setup.sh` waits for Caddy's CA cert file to actually exist before moving on
to any stack that needs it — don't remove that wait loop.

## The single-file bind-mount trap (hit three times tonight)

Docker will silently create a host path as an **empty directory** if a
compose file bind-mounts a single file that doesn't exist yet at that path.
The container then fails to start with `not a directory`, or — worse, if the
mount partially succeeds — just gets an empty file where content was
expected.

This hit: `/srv/docker/caddy/Caddyfile`, `/srv/docker/caddy/caddy-root.crt`,
and `/srv/docker/iptvorg-epg/channels.xml`. All three are now staged as real
files by `setup.sh` *before* their consuming containers first start.

**If you add a new single-file bind mount to any compose file**, make sure
the host file exists before the first `docker compose up -d`, or add a
staging step to `setup.sh` alongside the three above.

**Recovery if you hit this anyway:** `docker rm -f <container>` and
recreate via `docker compose up -d` — `docker restart` is not enough, because
the container's mount is pinned to the directory it was originally created
with (same root cause as [[gotcha-caddyfile-live-vs-mirror]] / the inode
pinning described in `docs/caddyfile-edit-severs-bind-mount.md`).

## Things setup.sh now handles that it didn't before

- **Per-stack `.env` files**, not one combined root `.env`. The repo moved to
  this convention after the 2026-08-23 Portainer migration; `setup.sh` never
  caught up until this rehearsal found the drift. `mealie` and `vaultwarden`
  use nonstandard filenames (`mealie.env`, `vaultwarden.env`) — check each
  stack's `docker-compose.yml` for its actual `env_file:` before assuming
  `.env`.
- **`SERVER_IP` is now auto-detected** (`ip route get 1.1.1.1`) instead of
  read from a file — it was the only genuinely cross-cutting value in the old
  root `.env`, used solely to template AdGuard's DNS rewrites.
- **`romm`'s external Docker volumes** (`33_mysql-data`, `33_romm-redis-data`,
  `33_romm-resources`). These exist on production only because Portainer
  auto-created them years ago before the repo migration — nothing in the
  repo creates them, so a fresh `docker compose up` for romm fails outright
  without this.
- **`openGym`'s application source**, which is a third-party upstream repo
  (`github.com/arvids-unavailable/openGym`), not part of this repo. It's now
  cloned automatically.
- **Stirling PDF's OCR languages.** Bind-mounting `tessdata` masks the
  image's built-in language packs entirely — the directory has to be seeded
  from the image first via a disposable container, then Spanish added on top
  (it's not in the base image). See `gotcha-stirling-tessdata-path` — the
  path Stirling actually reads (`/usr/share/tesseract-ocr/5/tessdata`) is
  **not** what upstream's docs say.
  - **Watch this if editing `setup.sh`'s seeding step**: the image has its own
    `ENTRYPOINT` (`tini -- /scripts/init.sh`). `docker run <image> sh -c
    "..."` only overrides `CMD`, not `ENTRYPOINT` — the shell command gets
    passed as an *argument* to the entrypoint and silently ignored, while the
    full app boots anyway. Must use `docker run --entrypoint sh <image> -c
    "..."`.
- **The NVIDIA Container Toolkit** is now a hard prerequisite check
  (`nvidia-smi`, `nvidia-ctk`) rather than something that fails opaquely
  later when `docker-daemon.json`'s `nvidia` runtime doesn't exist and
  `systemctl restart docker` breaks every container that was running.
- **Caddy's CA cert distribution** now covers all four consumers, not just
  Jellyfin — `nextcloud`, `immich`, `mealie`, and `audiobookshelf` all bind-
  mount `/srv/docker/caddy/caddy-root.crt` for `REQUESTS_CA_BUNDLE` /
  `OIDC_TLS_CACERTFILE`, and the old script only ever wrote the system trust
  store and Jellyfin's copy.
- **`seer`'s directory ownership.** It runs as `PUID=1000`/`PGID=1000` (like
  the other `arr-stack` services) but isn't a linuxserver.io image, so it
  doesn't self-chown on boot the way they do — a fresh, root-owned
  `/srv/docker/seer` crash-loops it with `EACCES` trying to write logs.

## Still manual (not automated by setup.sh, deliberately)

These are documented in the README's "One-time system config" section and
weren't touched by this rehearsal — do them separately:

- **Storage mounts**: copy `system/fstab`, edit the two UUID lines and the
  NAS IP for the new machine's actual disks, `mount -a`.
- **NAS export allowlist**: the NAS only allows NFS connections from IPs
  already in `/etc/exports` — the new machine's LAN IP has to be added there
  (UGOS web UI, or NAS SSH temporarily enabled) before `mount -a` will work.
  Forgetting this gives `access denied by server`, not a timeout.
- **UFW rules** from `ufw-rules.txt` (that file is `ufw status verbose`
  output to translate into `ufw allow` commands by hand, not a script to run
  directly). Remember: **published Docker container ports bypass UFW
  entirely** — only host-networked services (Plex) are actually governed by
  it.
- **Cron jobs** from `cron/root-crontab` — mostly Pterodactyl-specific
  (N/A unless this machine runs Wings), plus the weekly `apt upgrade` /
  reboot.
- **Tailscale**: install, `tailscale up --advertise-routes=192.168.0.0/24
  --accept-dns=false`, then approve the subnet route and set Split DNS in
  the admin console. A node that's been offline a long time may come back
  fully logged out (not just disconnected) if its clock has drifted enough
  to fail the coordination server's TLS handshake — see "Dead CMOS battery"
  below.
- **Docker install itself**: `curl -fsSL https://get.docker.com | sh` (the
  README already documents this correctly). Don't hand-roll the apt-repo
  steps — the GPG key from `download.docker.com/linux/ubuntu/gpg` must be
  piped through `gpg --dearmor`, not saved as-is; saving the raw
  ASCII-armored key gives `NO_PUBKEY` signature failures that apt silently
  works around by falling back to stale cached index data instead of
  actually failing loud.

## Dead CMOS battery / clock drift

Any machine that's been fully powered off (not just disconnected) for a long
stretch may come back with a wildly wrong clock — the symptom is Tailscale
reporting "logged out" with a TLS certificate error mentioning a time weeks
in the past, plus DNS resolution failing because `/etc/resolv.conf` (pointed
at Tailscale's MagicDNS resolver) has nothing to fall back to. Fix the clock
manually (`sudo date -s "..."`) before anything else — NTP itself often can't
resolve its own server hostnames until DNS is fixed, which is blocked until
Tailscale reconnects, which needs the clock fixed first.

## Don't leave a fresh rehearsal machine pointed at real shared data

If the new machine mounts the same NAS shares as production while you're
still testing deploys, every media-facing stack (Nextcloud, Immich, the
`*arr` stack, SABnzbd/qBittorrent, Aurral, romm, rreading-glasses/Calibre/
audiobookshelf, Jellyfin, Plex) comes up with its **own independent,
freshly-initialized database** pointed at the **same real files** production
already manages. Two uncoordinated app instances writing against one library
is exactly how filecache/database desync incidents happen. Rehearse against
an unmounted or empty pool, or stop those stacks immediately once you're
done verifying they deploy — don't leave them running.

## Burn-in (from `~/mediahub-arcade.md` Phase 5, Step 0)

Before trusting new hardware with production workload:

```bash
stress-ng --cpu $(nproc) --vm 4 --vm-bytes 80% --timeout 30m
```

```bash
git clone https://github.com/wilicc/gpu-burn.git && cd gpu-burn && make
./gpu_burn 1800
```

Watch temps throughout (`sensors`, `nvidia-smi dmon`) and check `dmesg -T |
grep -i thermal` afterward — a passing `gpu-burn` result only checks
`hw_slowdown`, not whether the CPU itself throttled. Both tools need
installing fresh on a new box: `apt install stress-ng lm-sensors`, and
`gpu-burn` needs the CUDA toolkit (`apt install nvidia-cuda-toolkit`) just to
compile — budget a few minutes for that download.

This is a synthetic full-load test, not a substitute for exercising the real
mixed workload (containers + transcode + emulation + Sunshine concurrently)
once the machine is actually in service.
