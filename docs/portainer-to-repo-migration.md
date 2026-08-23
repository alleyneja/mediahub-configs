# Migrating stack ownership from Portainer to this repo

**Status: in progress. Started 2026-08-23.**

## Why

Every service is built from a `docker-compose.yml`. The question this migration
settles is *where the authoritative copy of that file lives*.

As of 2026-08-23, **35 of 47 managed containers** had their real compose file
inside Portainer's own storage
(`/var/lib/docker/volumes/portainer_data/_data/compose/<id>/`), while this repo
held copies of varying accuracy. Consequences:

- **The repo lied.** `stacks/uptime-kuma/docker-compose.yml` describes Diun, but
  Diun runs from Portainer stack 30. Editing the repo file does nothing.
- **The rebuild story was broken.** A from-GitHub rebuild would fail for most
  services, and you'd only find out mid-rebuild.
- **No history.** `git log` explains why Vaultwarden is pinned to 1.37.2.
  Portainer-owned stacks have no history at all.
- **It caused a real near-miss.** RomM's repo copy would have created an empty
  database, because the two copies disagreed about the project name.

Portainer is **not** being removed. It keeps working as a UI — it shows and
manages containers regardless of who created them, so the web UI, logs, console,
Recreate button and phone access all stay. Only the ownership of the file moves.

## Procedure (per stack)

1. `sudo diff` Portainer's real compose against the repo copy. Reconcile any
   drift into the repo **before** touching anything.
2. Check for a `stack.env` in Portainer's stack directory. Its variables must
   exist in the repo stack's `.env` (see `.env.example` in each stack).
3. **Check volume types.** This is where data is lost:
   - *bind mounts* — safe, paths are absolute and project-independent.
   - *named volumes* — the name is prefixed with the project name, so changing
     the project orphans them. Pin with `name:` + `external: true`
     (see `stacks/romm/docker-compose.yml`).
   - *anonymous volumes* (64-hex names) — **most dangerous**, invisible in the
     compose file. Compose only carries them across a recreate if the project
     name is unchanged. Declare them explicitly before migrating.
4. Capture a rollback baseline: image SHA, mount list, on-disk data size.
5. `docker stop <c> && docker rm <c>`, then `docker compose up -d` from the repo
   stack directory.
6. Verify: same image SHA (no accidental upgrade), identical mount list, data
   size unchanged, and a **real content check** — not just an HTTP 200.
7. Delete the now-stale Portainer stack entry **in the Portainer UI**.
   *Do this only after verifying*, and be aware that deleting a stack in
   Portainer stops and removes its containers. It is safe here only because the
   running container no longer carries that stack's project label.

## Order

Lowest blast radius first, so the procedure is boring by the time it matters.

| Order | Stack | Risk | Status |
|-------|-------|------|--------|
| 1 | jellyfin | low - single container, bind mounts only | **Done 2026-08-23** |
| 2 | audiobookshelf | low - bind mounts only, sqlite | **Done 2026-08-23** |
| 3 | calibre-web-automated | medium - databases | **Done 2026-08-23** |
| 4 | sabnzbd (4) | low | **Done 2026-08-23** |
| 5 | plex (7) | low, but host networking + nvidia | **Done 2026-08-23** |
| 6 | romm (33) | medium - anonymous volume with save states | **Done 2026-08-23** |
| 7 | stack 9 (rreading-glasses + bookshelf + calibre-content) | medium - 197 GB postgres | **Done 2026-08-23** |
| 8 | arr-stack (5) | medium - 7 containers, stack.env | **Done 2026-08-23** |
| 9 | visibility (30) | medium - homepage credentials | **Done 2026-08-23** |
| 10 | scanopy (31) | medium | **Done 2026-08-23** |
| 11 | immich (29) | medium - 109 GB photos | **Done 2026-08-23** |
| 12 | nextcloud (17) | medium | **Done 2026-08-23** |
| last | authentik (18) | high - SSO outage affects many services | pending |
| last | adguardhome (3) | high - DNS for the whole LAN | pending |
| last | caddy | high - blips every proxied service | pending |

`romm` needs its anonymous `/romm/assets` volume (13 MB of save states and
screenshots) declared explicitly **before** it is migrated, or that data is
orphaned.

## Migrated

### jellyfin — 2026-08-23

Portainer stack 32 -> `stacks/jellyfin/`. The two compose files were already
byte-identical, so no reconciliation was needed. Three bind mounts, no named or
anonymous volumes, no `stack.env` — nothing could be orphaned.

Verified after: project label `jellyfin`, config file path in this repo, image
SHA unchanged (`1694ff06...`, so no accidental upgrade), all three mounts
identical including the read-only flag on `/mnt/media`, config directory still
11 GB, `jellyfin.db` still 311,525,376 bytes, and the API served
`ServerName: Jellyhub, Version 10.11.8, StartupComplete: true`.

**Still to do:** delete the stale Portainer stack 32 entry in the Portainer UI.

Unrelated but noted: Jellyfin still tracks `:latest`, so its running version is
whatever was last pulled. Pin it during a deliberate version bump, not during a
migration — one change at a time.

### audiobookshelf -- 2026-08-23

Portainer stack 10 -> `stacks/audiobookshelf/`. Five bind mounts, **zero**
volume-type mounts, no `stack.env`.

One drift, and it was in the repo's favour: Portainer hardcoded
`dns: 192.168.0.21`, the repo parameterises it as `dns: ${SERVER_IP}`. Rather
than assume those were equivalent, both files were rendered through
`docker compose config` and the **resolved** outputs diffed -- identical. The
repo version was kept. Confirmed after the migration that the running container
really does have `dns=[192.168.0.21]`, so the substitution took effect rather
than silently resolving to empty.

Stopped cleanly first (exit 143 = SIGTERM) so sqlite could checkpoint; no WAL
file remained and `pragma integrity_check` returned `ok` *before* the container
was removed.

Verified after: project label `audiobookshelf`, config path in this repo, image
SHA unchanged (`a52dc5db...`), all five mounts identical including the
read-only flag on the mounted Caddy CA cert, config still 44 MB, metadata still
15 MB, `absdatabase.sqlite` still 45,109,248 bytes, API reporting
`isInit: true, version 2.32.1`, database models loaded and listening on :80,
zero errors in the startup log.

**Still to do:** delete the stale Portainer stack 10 entry in the Portainer UI.

### calibre-web-automated -- 2026-08-23

Portainer stack 19 -> `stacks/calibre/`. **Note the directory name does not match
the service name** -- the repo dir is `calibre`, the container is
`calibre-web-automated`. The compose project is therefore now `calibre`.

The repo copy was *better* than Portainer's, not merely equal: identical once
resolved, but carrying the two comments that explain why the config is the shape
it is -- the `REQUESTS_CA_BUNDLE` replacement trap
(`docs/calibre-web-ca-bundle.md`) and the bind-the-ext4-branch-not-the-pool rule
(`docs/calibre-library-on-ext4.md`). Portainer's copy had neither. A rebuild from
Portainer would have silently dropped both warnings. This is the clearest single
argument for repo ownership found so far.

Backed up first, because this library has been truncated once before:
`sqlite3 .backup` (safe on a live DB, unlike `cp`) to
`/srv/docker/_backups/calibre/metadata-pre-migration-*.db`, then the backup
itself was verified -- `integrity_check ok`, 56 books, matching the source.
Plus a tar of `/srv/docker/calibre-web/config`.

Verified after: image SHA unchanged (`c31a738b...`), all five mounts identical,
library still on ext4 (`/dev/sda1`), `metadata.db` still 491,520 bytes with
`integrity_check ok` and 56 books, `app.db` `integrity_check ok` with 4 users,
`REQUESTS_CA_BUNDLE` correctly set, and the app serving 302 -> /login -> 200.

**Anonymous volume confirmed orphaned, exactly as the procedure predicts.**
`/cwa-book-ingest` moved from volume `266d2c72...` to a fresh `847ce239...`.
Both are empty here so nothing was lost -- but this is a live demonstration of
the mechanism that would silently strand RomM's 13 MB of save states. The old
volume still exists and can be removed once confirmed unwanted.

Unproven: the `xdg-desktop-menu` errors in the startup log could not be compared
against the old container's logs, which were removed with it. They are calibre's
headless desktop-integration noise and the app functions, but they were not
verified as pre-existing.

### sabnzbd -- 2026-08-23
Portainer stack 4 -> `stacks/sabnzbd/`. Resolved configs identical, no volumes,
no `stack.env`. Image SHA and mounts unchanged.

### plex -- 2026-08-23
Portainer stack 7 -> `stacks/plex/`. **Real drift found and reconciled toward the
running config**: Portainer's copy had `extra_hosts: threadfin=192.168.0.21`, the
repo copy did not. Plex is on host networking so it does not share the compose
network's DNS -- without that entry Live TV silently breaks while Plex itself
looks perfectly healthy. Added to the repo copy with a comment before migrating.

Verified after: `network_mode: host` (mandatory -- see the comment in the compose;
bridge networking makes clients report "server unreachable"), `runtime: nvidia`,
`extra_hosts` present, Quadro P400 visible inside the container, threadfin
resolving, and the same `machineIdentifier` so clients do not see a new server.

### romm -- 2026-08-23
Portainer stack 33 -> `stacks/romm/`. This is the stack the whole anonymous-volume
warning was written for, and it paid off.

`/romm/assets` held **13 MB in an anonymous volume no compose file mentioned** --
two Pokemon Mystery Dungeon save states from 2026-08-19. Migrating without
handling it would have created a fresh empty volume and stranded them.

Handled by copying the data to `/srv/docker/romm/assets` (verified byte-for-byte
with `diff -r` before switching) and declaring it as a bind mount, so it is now
visible, backup-able, and immune to a project rename. Same for the empty
`/romm/config`. Backup taken first to `/srv/docker/_backups/romm/`.

The three named volumes stayed pinned to their `33_` names via `external: true`.
Verified after: `33_mysql-data`, `33_romm-resources` and `33_romm-redis-data` all
attached, **no stray `romm_*` volumes created**, both save states present through
the bind mount, and the database holding 25 roms across 8 platforms -- the real
one, not a fresh empty.

### stack 9 -- rreading-glasses + bookshelf + calibre-content -- 2026-08-23

One Portainer file (stack 9) defining five services, but deployed under **two
different project names** -- `9` for the rreading-glasses pair and
`calibre-readarr` for the three book services. Migrating unified all five under
`rreading-glasses`.

The database is 197 GB, so it was stopped **last and with `docker stop -t 90`**
rather than the default 10-second grace, and the shutdown was confirmed in the
log (`checkpoint complete`, `database system is shut down`) before anything was
removed. Verified after: 195 GB `rreading-glasses` database present, app
connected and listening on :8788, all five image SHAs unchanged.

Note `rreading-glasses` exits with code 2 on SIGTERM rather than 0. That appears
to be its own signal handling, not a fault, but it is worth knowing.

Note also: this postgres runs with the password `rg_change_me_no_symbols` -- a
placeholder that was never changed. It is not exposed outside the compose
network, but it should be rotated.

### arr-stack -- 2026-08-23

Portainer stack 5 -> `stacks/arr-stack/`. Seven containers. The five unpackerr
API keys in Portainer's `stack.env` were hash-compared against this repo's
`.env` before migrating -- all five matched.

**The check that mattered:** unpackerr previously ran "healthy" for two weeks
while extracting nothing, because empty API keys silently overrode its config
file. Confirmed after migration that all five servers report `apikey:true`, and
that neither `0 servers` nor `Missing ... API Key` appears in the log.

### visibility -- 2026-08-23

Portainer stack 30 -> `stacks/uptime-kuma/`. Five services, previously split
**three ways**: `uptime-kuma` already repo-owned, `glances` under project `30`,
and `netdata`/`diun`/`homepage` under `visibility`.

Drift ran in both directions here, and one side was dangerous:

- Portainer's copy had **15 `HOMEPAGE_VAR_*` widget credentials hardcoded** that
  this repo's copy lacked entirely. Migrating as-is would have left every
  Homepage widget rendering BLANK -- tiles still present, just no data, which is
  very easy to miss. Recovered from the running container into the gitignored
  `.env` and parameterised in the compose, because this repo is public.
- This repo had **29 `.lan` extra_hosts entries** Portainer's copy lacked, which
  uptime-kuma uses to monitor those URLs. Kept.

After reconciling, the repo is a strict superset: 29 repo-only lines (all
extra_hosts) and zero Portainer-only lines. Verified after: all 15 credentials
present and none empty, AdGuard's widget returning real queried-domain data,
netdata keeping `pid=host` with CAP_SYS_ADMIN/CAP_SYS_PTRACE, and uptime-kuma
applying 30 extra_hosts.

### scanopy -- 2026-08-23
Portainer stack 31 -> `stacks/scanopy/`. Named volumes already carried the
`scanopy_` prefix and the repo dir is also `scanopy`, so the project name -- and
therefore the volume names and the compose-generated `-1` container suffixes --
stayed identical.

**Fixed a relative bind mount.** Both copies said `- ./data:/data`. A relative
bind resolves against whichever directory holds the compose file, so it had been
silently pointing at Portainer's internal stack dir, and would have moved again
on any relocation -- as well as putting service data inside a PUBLIC git repo.
Changed to an absolute `/srv/docker/scanopy/data`, matching every other service.
The directory was empty in practice; real state lives in the postgres volume.

Verified after: both named volumes reattached, no strays, 11 MB database with
the real schema (a fresh init is ~7 MB), server returning HTTP 200.

### immich -- 2026-08-23
Portainer stack 29 -> `stacks/immich/`. Previously split -- `immich-redis` was
already repo-owned while the other three came from Portainer.

The repo copy was again the more correct one: it assigns
`ipv4_address: 172.18.0.100` to `immich-redis`, which that container genuinely
has, while Portainer's copy omitted it entirely.

Postgres stopped last with `-t 90` and a confirmed clean shutdown. Verified
after: 109 GB photo library untouched, 440 MB database (a fresh init is ~8 MB)
holding 24,051 asset-job-status rows and 325 assets with EXIF, redis keeping
172.18.0.100, server returning version 2.5.6, no errors in 387 log lines.

The empty anonymous `/data` volume on immich-server was replaced with a fresh one
as expected. It held nothing -- the photos are a bind mount.

### nextcloud -- 2026-08-23
Portainer stack 17 -> `stacks/nextcloud/`. **Defused a latent :8443 landmine.**

Portainer's copy set `OVERWRITECLIURL=https://nextcloud.lan:8443`, but Caddy
serves on 443 and Nextcloud's own `config.php` had already been corrected by hand
to `https://nextcloud.lan`. So the running container carried a stale, wrong env
var that disagreed with the authoritative config file -- harmless until something
caused the image to reapply env to config.php, at which point the fix would have
silently reverted.

This repo's copy already had the corrected value, so migrating removed the stale
env. Env and config.php now agree. `config.php` was backed up to
`/srv/docker/_backups/nextcloud/` first.

Verified after: installed, version 29.0.16, not in maintenance mode, 16 MB
database with 1,484 oc_filecache rows.
