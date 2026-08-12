# Mealie on mediahub-production — Design

**Date:** 2026-08-12
**Status:** Approved, ready for implementation planning
**Owner:** Jay

## Goal

Run [Mealie](https://mealie.io) (self-hosted recipe manager) on mediahub-production so
Jay and Mafe can add, edit, and plan recipes together, sharing a single recipe
collection, meal plan, and shopping list.

## Requirements

1. Two users (Jay, Mafe) sharing recipes **and** meal plans **and** shopping lists.
2. PostgreSQL backend, consistent with Immich / Nextcloud / Authentik.
3. Authentik OIDC login.
4. Recipes added primarily by URL scraping from public cooking sites.
5. Data must remain portable — leaving for Tandoor later must stay possible.

## Non-goals

- Backups. Handled separately as a mediahub-wide effort. See "Backup handoff" below.
- Fixing Calibre-Web's unrelated SSO defect (see "Out of scope findings").
- Importing an existing recipe collection. Starting fresh.

---

## Architecture

Two containers, repo-managed at `~/mediahub-configs/stacks/mealie/`, following the
Vaultwarden pattern rather than the Portainer-managed pattern, so the stack lives in
this repo per the standing commit rule.

| Container | Image | Role |
|---|---|---|
| `mealie` | `ghcr.io/mealie-recipes/mealie:v3.22.0` | Application |
| `mealie-postgres` | `postgres:16-alpine` | Database |

**Version pinned, not `:latest`**, per the no-blanket-auto-update policy. Diun already
watches the stack and will report new releases for a deliberate, scoped bump.

**`postgres:16-alpine`** is already run by Nextcloud and Authentik — a known quantity
for patching and restore.

**Dedicated Postgres instance** rather than sharing an existing one: Nextcloud's and
Authentik's databases are load-bearing. Isolation means a bad Mealie migration cannot
touch the password manager or personal cloud, and dump/restore stays scoped to one
app. Cost is roughly 30 MB RAM. A shared "apps" Postgres would be justified at ten
apps, not one.

### Storage

All on the NVMe (`/`, 1.4 TB free). **Nothing on the mergerfs pool or the NAS.**

| Path | Contents |
|---|---|
| `/srv/docker/mealie/data` | Recipe images, uploads, Mealie-generated backups |
| `/srv/docker/mealie/pgdata` | PostgreSQL data directory |

**Why not `/mnt/media`:** the pool runs `cache.files=off`, which blocks `mmap` and
breaks database engines. This is the documented root cause of the Calibre library
move to ext4 (`docs/calibre-library-on-ext4.md`) and the qBittorrent POSIX disk I/O
fix (`docs/qbittorrent-posix-disk-io.md`).

**Why not the NAS:** database files require POSIX byte-range locking, `mmap`, and
honest `fsync`. NFS provides weak versions of all three; the failure mode is silent
corruption, not a dropped mount — the same class of failure as the Calibre
`metadata.db` truncation. The NAS also has precedent for stalling under load (nfsd
thread starvation, July 2026). A stalled NAS holding recipe photos means missing
thumbnails; a stalled NAS holding the Postgres data directory means a hung or
corrupted application.

**Sizing.** Recipe text is negligible; images dominate at roughly 400 KB–1 MB per
recipe with a photo. 1,000 recipes is approximately 725 MB — about 0.05% of free NVMe.
Splitting images to the NAS would save under a gigabyte while adding a mount
dependency, so images stay local. These are estimates from typical WebP sizes; actual
per-recipe cost should be measured once ~20 real recipes exist.

### Networking

- Network: `mediahub_internal` (external).
- **No published host port.** Access is via Caddy only.
- `dns: 192.168.0.21` (AdGuard), matching Immich / Audiobookshelf / Nextcloud, so the
  container can resolve `auth.lan`.
- Caddy gains a `mealie.lan` block with `tls internal`, reverse-proxying `mealie:9000`.
- Remote access for Mafe works over Tailscale with no extra configuration; tailnet DNS
  is already set up.

> **Do not** use `extra_hosts` to pin `auth.lan` to an IP. See "Out of scope findings".

---

## Data model: groups and households

Mealie's hierarchy is **Group → Household → Users**.

| Scope | Shared resources |
|---|---|
| Group | Recipes, tags, categories, tools, foods, units |
| Household | **Meal plans, shopping lists**, integrations |

Jay and Mafe must be in the **same group *and* the same household**. Same group but
different households would share recipes while splitting meal plans and shopping
lists — the wrong shape for two people cooking together.

Separate accounts (rather than a shared login) still give individual favorites,
ratings, and an edit trail, at no cost to sharing.

**Open item:** Mealie's documentation does not state which group and household
OIDC-created users are assigned to. This is not assumed — it is an explicit verification
gate in the build sequence (step 7).

---

## Authentication: Authentik OIDC

### Authentik side

- **Group `mealie-users`** — contains `alleyneja` and `mafer_107`. Gates who may log in.
- **Application + OAuth2/OpenID provider "Mealie"**, authorization code flow with PKCE.
- **Redirect URIs:**
  - `https://mealie.lan/login`
  - `https://mealie.lan/login?direct=1`
- **Admin group:** reuse the existing `admin` group. Jay is a member, Mafe is not, so
  Jay becomes a Mealie admin and Mafe a standard user. No new admin group needed.
- **Scopes:** `openid profile email`. Authentik's default `profile` scope mapping
  already emits a `groups` claim (`[group.name for group in request.user.ak_groups.all()]`),
  so `OIDC_GROUPS_CLAIM=groups` works with no custom property mapping.

**Client type divergence.** The five existing providers (Audiobookshelf, Nextcloud,
Calibre-Web, Immich, Jellyfin) are all `confidential`. Mealie's documentation specifies
a **public** client with PKCE, because its auth flow runs browser-side. Follow Mealie's
documentation rather than the house pattern; a mismatch here surfaces as opaque
`invalid_client` errors. Mealie supports `OIDC_CLIENT_SECRET` as a fallback if the
public-client flow proves problematic.

### Mealie side

Non-secret variables in `docker-compose.yml`. **`POSTGRES_PASSWORD` and
`OIDC_CLIENT_ID` go in `mealie.env`**, which is gitignored via the `*.env` rule.
`mediahub-configs` is a **public** repository and has leaked a secret before
(Vaultwarden `ADMIN_TOKEN`), so this is non-negotiable.

Because the provider is a **public** client using PKCE, there is no
`OIDC_CLIENT_SECRET` to store. If the public-client flow has to be abandoned for a
confidential client, the resulting secret goes in `mealie.env` as well.

Database and runtime variables:

```
POSTGRES_SERVER=mealie-postgres
POSTGRES_PORT=5432
POSTGRES_USER=mealie
POSTGRES_DB=mealie
PUID=1000
PGID=1000
```

`POSTGRES_SERVER` must match the database container name. `PUID`/`PGID` are set to
`1000` (jay) rather than Mealie's `911` default, so the container's writes to
`/srv/docker/mealie/` match the ownership used across `/srv/docker`.

OIDC and application variables:

```
OIDC_AUTH_ENABLED=true
OIDC_CONFIGURATION_URL=https://auth.lan/application/o/mealie/.well-known/openid-configuration
OIDC_PROVIDER_NAME=Authentik
OIDC_USER_GROUP=mealie-users
OIDC_ADMIN_GROUP=admin
OIDC_GROUPS_CLAIM=groups
OIDC_SIGNUP_ENABLED=true
OIDC_REQUIRES_EMAIL_VERIFICATION=false
OIDC_TLS_CACERTFILE=/etc/ssl/certs/caddy-root.crt
ALLOW_PASSWORD_LOGIN=true
ALLOW_SIGNUP=false
BASE_URL=https://mealie.lan
TZ=America/Chicago
```

Plus a read-only mount of the shared CA:

```
- /srv/docker/caddy/caddy-root.crt:/etc/ssl/certs/caddy-root.crt:ro
```

(Verified 2026-08-12: this file's SHA-256 matches Caddy's live internal root at
`/data/caddy/pki/authorities/local/root.crt`.)

### Three deliberate choices

**`OIDC_TLS_CACERTFILE`, not `REQUESTS_CA_BUNDLE`.** Mealie is a Python application
doing two distinct TLS jobs: talking to Authentik over the Caddy internal CA, and
**scraping recipes from public cooking sites** over public CAs. `REQUESTS_CA_BUNDLE`
*replaces* Python's trust store rather than adding to it — pointing it at
`caddy-root.crt` alone would make SSO work while silently breaking every recipe
import. That is precisely the failure Calibre-Web hit with cover and metadata lookups
(`docs/calibre-web-ca-bundle.md`). `OIDC_TLS_CACERTFILE` is scoped to the OIDC client
only and leaves the default bundle intact.

**`ALLOW_PASSWORD_LOGIN=true`.** Retains the local admin account alongside SSO. Mealie
is the first service on this stack to use Authentik OIDC directly, so a working
non-SSO door matters. Keep it enabled permanently, not only during setup.

**`OIDC_REQUIRES_EMAIL_VERIFICATION=false`.** Defaults to `true` as of Mealie v3.21.0
and requires the IdP to assert `email_verified`. Starting with it disabled removes one
variable from first-login debugging. It can be enabled after the flow is proven.

---

## Build sequence

Each step has a gate. Do not proceed past a failing gate.

| # | Step | Gate |
|---|---|---|
| 1 | Scaffold `stacks/mealie/` and `/srv/docker/mealie/{data,pgdata}` | `docker compose config` renders; no secrets in the tracked file |
| 2 | Bring up Postgres + Mealie, **OIDC disabled**, local admin only | Container healthy; admin login works; pgdata populated on NVMe |
| 3 | Add Caddy `mealie.lan` block, reload | `https://mealie.lan` serves with a valid internal cert |
| 4 | **Import one recipe by URL** | A real recipe with image imports cleanly |
| 5 | Create Authentik group, provider, application | Discovery URL fetches successfully *from inside the Mealie container* |
| 6 | Enable OIDC variables, restart | SSO login works **and** local admin still works **and** recipe scraping still works |
| 7 | Mafe logs in via SSO | Admin UI confirms both users share one group **and one household**; move her if not |
| 8 | Homepage tile, Uptime Kuma monitor, Diun watch | Service appears and reports healthy |
| 9 | Commit to `mediahub-configs` | Standing commit rule satisfied |

**Step 4 is deliberately before step 6.** Proving public-CA scraping works *before*
introducing any TLS configuration means that if scraping breaks afterward, OIDC
configuration is the known cause. Step 6's gate re-checks all three behaviors for the
same reason.

Containers use `restart: unless-stopped`. Note the root crontab runs
`apt upgrade -y && reboot` at 03:00 on Sundays and Wednesdays, so the stack must
survive unattended reboots. Per `docs/unpackerr-stopped-state.md`, an explicitly
stopped container stays stopped across reboots — if Mealie is ever manually stopped,
it must be explicitly started again.

---

## Backup handoff

Out of scope here; backups are handled as a separate mediahub-wide effort.

Two facts that effort needs:

1. Mealie performs **no automatic backups**. Upstream: *"Mealie no longer performs
   automatic backups; it is advised that during setup you also set up a backup
   strategy."*
2. The two paths that must be covered are **`/srv/docker/mealie/pgdata`** and
   **`/srv/docker/mealie/data`**. A `pg_dump` is preferable to a raw copy of `pgdata`.

---

## Exit path (portability)

Requirement 5 is real but **not push-button**. Tandoor's Mealie importer exists and is
documented as unreliable: open issues report imports completing with zero recipes and
no error shown ([#3186](https://github.com/TandoorRecipes/recipes/issues/3186)), and
imports that bring in steps and details while dropping images, ingredients, foods, and
units ([#4071](https://github.com/TandoorRecipes/recipes/issues/4071)).

Portability therefore rests on retaining three things rather than trusting one button:

1. **`pg_dump`** — complete structured data, restorable to any Postgres and convertible
   regardless of upstream tooling.
2. **Mealie's JSON recipe export** (group data management) — the portable interchange
   format, and what third-party converters consume. Run manually once the collection is
   worth protecting.
3. **The images directory** — plain files, portable by definition.

If Tandoor is wanted later, the honest approach is to stand it up alongside Mealie and
test the import against a copy, rather than migrating and hoping.

---

## Out of scope findings

**Calibre-Web's Authentik SSO is likely broken.** Its compose sets
`extra_hosts: auth.lan:172.18.0.28`, but `172.18.0.28` is the **netdata** container —
Caddy is at `172.18.0.200`. `/etc/hosts` takes precedence over DNS, so that container
resolves `auth.lan` to the wrong destination. Left untouched at Jay's direction;
revisit if login problems appear. Mealie must not copy this pattern — it uses
`dns: 192.168.0.21` instead.

---

## References

- [Mealie backend configuration](https://docs.mealie.io/documentation/getting-started/installation/backend-config/)
- [Mealie OIDC setup](https://docs.mealie.io/documentation/getting-started/authentication/oidc/)
- [Mealie features / groups and households](https://docs.mealie.io/documentation/getting-started/features/)
- [Mealie v2.0.0 release — Households](https://github.com/mealie-recipes/mealie/releases/tag/v2.0.0)
- [Tandoor import/export](https://docs.tandoor.dev/features/import_export/)
