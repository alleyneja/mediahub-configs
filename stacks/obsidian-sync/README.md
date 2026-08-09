# obsidian-sync — CouchDB backend for Obsidian Self-hosted LiveSync

Backs three Obsidian vaults for Jay and Mafe. Built 2026-08-08.

| Vault | CouchDB database | Who has access |
|---|---|---|
| Casa (shared) | `casa` | jay, mafe |
| Jay's private | `jay` | jay only |
| Mafe's private | `mafe` | mafe only |

## Architecture

- CouchDB 3.4 on the existing `mediahub_internal` network, **no published ports** —
  reachable only via Caddy at `obsidian-sync.lan`.
- Data at `/srv/docker/couchdb` on **NVMe**. Never put this on `/mnt/media`: the mergerfs
  pool runs `cache.files=off`, which blocks mmap and corrupts databases (this is what broke
  Calibre — see `docs/`).
- `local.d/obsidian-livesync.ini` holds the CORS origins and request-size limits LiveSync
  requires, bind-mounted so it survives container replacement.
- Admin password in `.env` (gitignored). **This repo is public** — never inline secrets.

**Do not put Authentik forward-auth in front of `obsidian-sync.lan`.** LiveSync uses HTTP
basic auth and cannot complete an interactive browser login; forward-auth breaks sync
silently.

## Rebuilding the CouchDB state from scratch

The compose file does not create databases, users, or permissions. After a fresh deploy:

```bash
CU="http://admin:$COUCHDB_PASSWORD@127.0.0.1:5984"
R() { docker exec couchdb curl -s -X "$1" "$CU$2" ${3:+-H "Content-Type: application/json" -d "$3"}; }

# system databases
for db in _users _replicator _global_changes; do R PUT "/$db"; done

# vault databases
for db in casa jay mafe; do R PUT "/$db"; done

# users (passwords from Vaultwarden — keep them word-style, see gotcha below)
R PUT "/_users/org.couchdb.user:jay"  '{"name":"jay","password":"<PW>","roles":[],"type":"user"}'
R PUT "/_users/org.couchdb.user:mafe" '{"name":"mafe","password":"<PW>","roles":[],"type":"user"}'

# per-database access. Users need db-admin (not just member) or LiveSync's
# "Initialise Server" step fails with 401 on DELETE and setup half-completes.
R PUT "/casa/_security" '{"admins":{"names":["jay","mafe"],"roles":["_admin"]},"members":{"names":["jay","mafe"],"roles":[]}}'
R PUT "/jay/_security"  '{"admins":{"names":["jay"],"roles":["_admin"]},"members":{"names":["jay"],"roles":[]}}'
R PUT "/mafe/_security" '{"admins":{"names":["mafe"],"roles":["_admin"]},"members":{"names":["mafe"],"roles":[]}}'
```

Verify isolation afterwards — cross-vault reads must return 403 and anonymous 401:

```bash
R="--resolve obsidian-sync.lan:443:127.0.0.1 -sk"
curl $R -u jay:<PW>  -o /dev/null -w '%{http_code}\n' https://obsidian-sync.lan/mafe/   # want 403
curl $R -u mafe:<PW> -o /dev/null -w '%{http_code}\n' https://obsidian-sync.lan/jay/    # want 403
curl $R              -o /dev/null -w '%{http_code}\n' https://obsidian-sync.lan/casa/   # want 401
```

Also needed outside this stack: the Caddy vhost (in `caddy/Caddyfile`, but note the **live**
file is `/srv/docker/caddy/Caddyfile` — the repo copy is a mirror) and the AdGuard rewrite
`obsidian-sync.lan -> 100.104.43.6`.

## Client gotchas, learned the hard way

- **Add new devices with the QR code / Setup URI, not the manual wizard.** Manual setup
  failed ~8 times on one iPhone: it authenticated fine, then never issued a second request
  and the wizard looped back to "Welcome". The QR import worked first try.
- **Diagnostic:** compare endpoints per device in the CouchDB log. A working client hits
  ~13 (`obsydian_livesync_version`, `_local/obsidian_livesync_sync_parameters`, `_changes`,
  `_revs_diff`, `_bulk_docs`). A broken one hits only `GET /<db>/`.
- **The Setup URI embeds the generating user's credentials.** A device set up from Jay's QR
  authenticates as `jay`. Fix afterwards in Remote Database Configuration; required before
  that device can use its own private vault, since `jay` is 403 on `mafe`.
- **Sync Mode is not set by the wizard.** It defaults to unset and nothing replicates, with
  no error. Sync Settings → Sync Mode → LiveSync.
- **Community plugins are per-vault.** Each new vault needs LiveSync installed and
  configured again from scratch.
- **CouchDB 3.4 locks accounts** after repeated auth failures ("Account is temporarily
  locked"), and logs the user as `undefined`, which looks like a permissions bug. Clears
  itself in a few minutes or instantly via `docker restart couchdb`. Keep user passwords
  **word-style and phone-typable** — long random strings get mistyped on a phone keyboard
  and trigger this. Long random is fine for the admin password and the E2EE passphrases,
  which are not hand-entered repeatedly.
- **Never pair LiveSync with another sync layer.** Vaults must be stored *on the device*,
  not in iCloud Drive.

## Encryption

E2EE is on for all three vaults. Stored documents carry `"e_":true` with ciphertext in
`data`, and content chunk ids are hashed (`h:...`). Verified: no readable English appears
in stored data, so the server admin cannot read vault contents.

**Path obfuscation is deliberately off.** Filenames are therefore visible to anyone with
database access (e.g. `groceries.md` appears as a plaintext document id). This was a
conscious trade for troubleshooting visibility, agreed by both vault owners. Enabling it
per-vault requires rebuilding that database.

The E2EE passphrase is **per vault** (both Casa devices must use the identical string); the
CouchDB username/password is **per person**. Losing a passphrase destroys only the
server-side copy — local vaults are plain markdown and stay readable, so recovery is: copy
from a good device, new database, new passphrase.

## Credentials

All in Vaultwarden. Nothing here, nothing in git.
