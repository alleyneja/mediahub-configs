# API key rotation (done 2026-10-08, issue #64)

Rotated headlessly, one service at a time, each step verified (new key = 200, old key = 401/403) before the next:
Bazarr, Seerr, SABnzbd, Prowlarr, Sonarr, Radarr, Lidarr, Bookshelf ebooks, Bookshelf audiobooks. Each service was down ~10-30 s.
Backups of the old configs (now-dead keys): `~/key-rotation-backup-20261008/` (mode 700; delete once Jay is satisfied).
**Vaultwarden ADMIN_TOKEN rotated 2026-10-11 (issue #38):** new random token, stored as an Argon2id hash (`vaultwarden hash --preset owasp`) in gitignored `stacks/vaultwarden/vaultwarden.env`; plaintext in gitignored `stacks/vaultwarden/vaultwarden-admin-plaintext.env` (mode 600). Gotcha: compose `env_file` interpolates `$`, so the hash MUST be single-quoted (`ADMIN_TOKEN='$argon2id$v=19$...'`) or the container gets a mangled value and crash-loops on "invalid Base64 encoding". Verified: new token 200, wrong token 401, live container env == file.
**Not rotated here (need a human decision/UI): AdGuard login, qBittorrent password (deliberately deferred until Vaultwarden rollout, #64), Immich API keys (done below).**
**Immich API key rotated 2026-10-11 (#64):** only consumer is the Homepage widget (`HOMEPAGE_VAR_IMMICH_KEY` in gitignored `stacks/uptime-kuma/.env` -> `services.yaml`). Replaced the single `all`-permission key with a scoped one (`server.statistics` only is what the v2 widget actually calls; `server.about` also granted) created headlessly via `POST /api/api-keys` using the old key, then old key deleted (now 401). Widget mapping names are `statistics_v2` / `version_v2`; `server/version` needs no permission.

## Where each key lives (the consumer map)
| Key | Own config | Also stored in |
|---|---|---|
| sonarr / radarr | `/srv/docker/<app>/config.xml` `<ApiKey>` | Prowlarr Applications, Bazarr `config.yaml`, Seer `settings.json` (+ `settings.old.json`, stale `/srv/docker/overseerr/`), `stacks/arr-stack/.env` (Unpackerr `UN_*_API_KEY`), `stacks/uptime-kuma/.env` (Homepage `HOMEPAGE_VAR_*`) |
| lidarr, bookshelf-ebooks/-audiobooks | same pattern | Prowlarr Applications, `arr-stack/.env`, `uptime-kuma/.env`; lidarr also **Aurral** (see below) |
| aurral (consumer of lidarr's key) | `/srv/docker/aurral/data/aurral.db`, table `settings`, key `integrations` JSON field `lidarr.apiKey` (stored encrypted, `AURRAL_ENC:`) | not in env or any file: update it by hand in Aurral **Settings > Lidarr**. Missed in the 2026-10-08 rotation, fixed by hand 2026-10-09 (issue #74) |
| prowlarr | `config.xml` | every *arr's synced indexers (`(Prowlarr)` entries, field `apiKey`), `uptime-kuma/.env` |
| sabnzbd | `sabnzbd.ini` `api_key` | the SABnzbd download client in all 6 apps, Kuma monitor 7 (URL contains it), `uptime-kuma/.env` |
| bazarr | `config/config.yaml` `apikey` | `uptime-kuma/.env` |
| seerr | `/srv/docker/seer/settings.json` `main.apiKey` | `uptime-kuma/.env` |
`.env` files are gitignored. Homepage reads its keys from `stacks/uptime-kuma/.env` (recreate with `docker compose up -d --no-deps homepage`).

## Gotchas found
- *arr APIs (v3/v4) **mask** `apiKey` fields in responses, so you cannot find "which client holds the key" by value; set the new value on every relevant entry instead (PUT with `?forceSave=true`). The Bookshelf fork returns them in plain text.
- **Rotating Prowlarr does not push the new key to the apps' synced indexers** (the "sync" command completes but leaves 401s). Set `apiKey` on every `*(Prowlarr)` indexer in each app yourself.
- Prowlarr's link to bookshelf-**audiobooks** uses internal port 8787 (same as ebooks): match Prowlarr applications by hostname (`audiobooks` / `ebooks`), not by port.
- The running Seerr container uses `/srv/docker/seer/` (mounted at `/app/config`); `/srv/docker/overseerr/` is a stale copy.
- Kuma's database is root-owned and ~600 MB: stop the container, `sudo sqlite3`, start.
- Compose: use `docker compose up -d --no-deps <service>` from the stack dir so `.env` is read and only that container is recreated.
- Aurral's Lidarr key lives encrypted in its own database, so grepping env/config files will not find it. After any Lidarr rotation, check `docker logs aurral | grep 'Lidarr API error (401)'` and re-enter the key in Aurral Settings.
- Do not run `testall` repeatedly against indexers: it triggers 429s (TorrentGalaxy) and makes health look worse than it is.

## Verification used
New key accepted / old rejected on every service; `downloadclient/testall` in all 6 apps; Prowlarr `applications/testall`; consumers' own logs (Unpackerr "Updated" lines); then a Loki query across all containers for `401:Unauthorized` / `API key incorrect` after the change (the weekly digest would also catch stragglers).
Pre-existing, unrelated: TorrentGalaxyClone / MoviesDVDR / The Pirate Bay indexers failing (health warning "for more than 6 hours"). Update 2026-10-10: TorrentGalaxyClone, MoviesDVDR and Elitetorrent-wf were removed upstream (definitions deleted from Prowlarr/Indexers v11) and have been deleted from Prowlarr; not a key-rotation or AdGuard problem.
