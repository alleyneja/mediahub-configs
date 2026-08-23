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
| ... | arr-stack (5), romm (33), immich (29), nextcloud (17) | medium | pending |
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
