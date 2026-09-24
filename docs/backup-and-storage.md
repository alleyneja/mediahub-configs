# Backup and storage

**Status:** DRAFT 2026-09-21, in progress. Requirements-first conversation (see the fleet doc's
decision-recording standard, §5d) prompted by finding that the nightly database backups all sit
on the same drive as what they protect.

## 1. Requirements

| # | Requirement | Origin |
|---|---|---|
| B1 | Sort everything into three tiers: **irreplaceable** (photos, the Vaultwarden vault, Nextcloud's files, game saves, secrets/certificate keys), **replaceable but painful** (the 11 TB media library, ROMs, Plex's watch history), **disposable** (caches, thumbnails, container images). Design follows from this. | Jay, 2026-09-21 |
| B2 | A Vaultwarden vault loss would be a real problem for Jay. Treat it as the highest-priority item to protect properly. | Jay, 2026-09-21 |
| B3 | One mechanism for versioning, encryption and deduplication, with one alerting pattern (the Kuma push-heartbeat pattern already built for Immich), rather than five different ad hoc scripts. | Jay, 2026-09-21 |
| B4 | Free or cheap solutions first. Some threats (a second NAS drive, an off-site drive at a relative's home) cost real money and can wait. | Jay, 2026-09-21 |
| B5 | Any off-site/cloud copy should be **small**: the critical items only, never the 80 GB photo library or the 11 TB media library. | Jay, 2026-09-21 |
| B6 | Off-site destination: Jay's own Google account (Google Drive, 15 GB free), not a new paid service. | Jay, 2026-09-21 |
| B7 | Scope for now is Jay's own data. Mafe's data (her share of Vaultwarden, Nextcloud) is a later, explicit decision, not assumed. | Jay, 2026-09-21 |
| B8 | The encryption key for the off-site backup must not live only inside something this backup protects (specifically: not only in Vaultwarden). Jay's Apple ID / iCloud Keychain is an acceptable independent place to hold it, alongside a physical paper copy. | Jay, 2026-09-21 |

### Facts gathered before designing anything

- The five existing nightly jobs (Immich, Nextcloud, Vaultwarden, Authentik, Calibre) all write their dumps to `/srv/docker/*/backups` **on production's own NVMe** -- the same drive that holds the live data. They protect against a bad upgrade or an accidental delete, not against that drive failing.
- Nextcloud's existing backup explicitly does **not** include the data directory (actual files) -- only the database and `config.php`. Actual files: 741 MB across both pool branches.
- Sizes measured 2026-09-21: Vaultwarden vault 2.1 MB; Nextcloud files 741 MB; Authentik data 144 MB; r9 saves mirror 194 MB; Caddy/SSH keys under 1 KB; Immich's own DB dump history ~1.9 GB (14 kept). A full restic snapshot of all of it together (live files, not just the samples) came to **2.5 GB**.
- The repo (`mediahub-configs`) already pushes to GitHub and was current when checked -- config and scripts are already off-site, for free, today.

---

## 2. Decision D1: off-site mechanism is restic, over rclone, to Google Drive

**What:** `scripts/offsite-backup.sh` (repo) takes a nightly [restic](https://restic.net/) snapshot of the sources below, through [rclone](https://rclone.org/)'s Google Drive backend, to a personal Google account. Runs at **03:00**, after the other nightly dump jobs (02:30-02:50) finish, so it reads finished dumps rather than a live database. The repository password lives in `scripts/offsite-backup.env` (gitignored, mode 600) for the script's own use, and separately in Jay's Apple Keychain and on paper -- not only in this file, and not in Vaultwarden.

**Sources (chosen to avoid reading a live database file directly):**
- `/srv/docker/vaultwarden/backups` -- Vaultwarden's own consistent SQLite backup
- `/srv/docker/nextcloud/backups` -- Nextcloud's DB dump
- `/mnt/media/nextcloud` -- Nextcloud's actual files (a live tree; low risk, not a database)
- `/srv/docker/authentik/backups` -- Authentik's DB dump
- `/srv/docker/immich/backups` -- Immich's DB dump history
- `/mnt/media/arcade/backups/r9-saves` -- the nightly r9-to-production saves mirror (D2/Phase 2)
- `/srv/docker/caddy/data/caddy/pki/authorities/local` -- the Caddy certificate authority's root and intermediate keys
- `/home/jay/.ssh` -- SSH keys

**Why restic:** it gives versioning, encryption and deduplication in one tool (satisfies B3), is free and widely used, and its own `check --read-data` and `restore` commands make verification straightforward rather than another thing to build.

**Why these sources, not the raw database directories:** each database already has its own script producing a consistent snapshot (`pg_dump`, `sqlite3 .backup`); reading its live data files directly risks a torn read mid-write. Reading the dumps it already produces avoids that at no extra cost.

**Retention:** 30 daily snapshots plus 12 monthly, pruned automatically by `restic forget --prune` -- restic manages this itself rather than a separate cleanup script.

**Tested 2026-09-21, before anything was pointed at Google:**
1. A scratch local repository: init, backup of one real target (Vaultwarden's vault), `check --read-data` (0 errors), restore, and a `diff -rq` against the source -- exact match.
2. A first full run of the **actual, unmodified script** (pointed at a scratch repository) caught a real bug: `sudo` does not inherit exported environment variables, so `sudo -n restic ...` after `export RESTIC_PASSWORD` would have failed every night. Fixed by passing the variables explicitly (`sudo -n env RESTIC_PASSWORD=... RESTIC_REPOSITORY=... restic ...`).
3. After the fix: full run against a scratch repository, all 8 sources, exit 0, `check --read-data` 0 errors across 146 packs, and a `diff -rq` of every one of the 8 source paths against the restored copy -- all matched exactly. Total: 1,246 files, 2.5 GB, stored as 2.4 GB, in 31 seconds.

**What actually happened live, 2026-09-21 evening:**
1. Jay connected his Google account via the two-machine `rclone authorize` flow (Google's OAuth requires a browser; production is headless, so the token was generated on Jay's gaming PC and pasted across). The interactive `rclone config` wizard corrupted the long pasted token mid-transit (a pty/terminal artifact, not a wrong value) and silently saved an empty one, reporting "Configuration complete" regardless -- caught only because the plan called for testing against real Google servers rather than trusting that message. Fixed by writing the token directly via `rclone config update`, then verified for real: account quota, a folder listing, and a full write/list/read/delete round trip against the live Drive.
2. **Security incident, found and fixed the same night:** the first version of the script's `run_restic()` passed the encryption password via `sudo -n env RESTIC_PASSWORD=... command`, which puts it in that process's command line -- visible to any local user via `ps` for as long as the process runs (a different, wider exposure than a chat transcript or a log file). Caught while watching a live run. Remediated in full before relying on the repository: the password was rotated by adding a new restic key and removing the old one (`restic key list`/`key add --new-password-file`/`key remove`, authenticating the final removal with the new key so it could not lock itself out), and the script was rewritten to use `restic --password-file` (which restic reads directly and which never appears in `ps` or argv). Confirmed with a live process-list check during a real run that no secret appears. Nothing was ever written to a log file or committed to git; the exposure window was the few minutes those specific commands ran.
3. **The full 2.5 GB backup failed twice**, both times late, after a long spell of retries, with `googleapi: Error 403 ... rateLimitExceeded ... quota_metric: drive.googleapis.com/default`. Root cause: rclone's Google Drive backend defaults to a **shared client ID** when none is configured (the wizard's own text: "it will use an internal key which is low performance") -- its quota is shared across every rclone user worldwide who has not configured their own, and it was exhausted. Not specific to this account, this network, or the data.
4. **Real fix identified, not yet applied:** [rclone's own documentation](https://rclone.org/drive/#making-your-own-client-id) covers creating a personal Google Cloud OAuth client ID, which gets its own dedicated quota. Free, roughly 5-10 minutes, no new Google login (it is a project/credentials setup on the same account). Deferred to another session (Jay: "let's just do Vaultwarden tonight").
5. **What is actually protected off-site tonight:** just the Vaultwarden vault dump (`/srv/docker/vaultwarden/backups`, 3.7 MiB), backed up with tag `vaultwarden-only`, restored from the real Google Drive repository afterward, and diffed byte-for-byte against the source -- matched exactly. `restic check` then found 11 orphaned data packs (550 MB) left by the two failed full-backup attempts (uploaded data whose snapshot record never got written); `restic prune` removed them. Drive now holds only the one real snapshot.

**Split into per-source attempts (Jay's idea, since the bundled full run kept failing as one giant batch):** ran each of the 8 sources as its own `restic backup` call against the real repository, smallest first, to see how far this got without waiting for the client ID fix.

**Result: 6 of 8 sources are off-site tonight, verified present in `restic snapshots`:**
`vaultwarden-only`, `caddy-keys`, `ssh-keys`, `nextcloud-db`, `authentik-db` (75 MB, needed one retry), `r9-saves` (194 MB, failed once with the same `rateLimitExceeded` error, succeeded on a straight retry).

**2 of 8 did not make it, and are still blocked on the same root cause:**
- `/mnt/media/nextcloud` (actual files, ~741 MB): ran for about 40 minutes, genuinely uploading (confirmed via `/proc/<pid>/io`, not stalled) but never finished; stopped deliberately given the hour rather than left running indefinitely. No partial snapshot was left behind (restic only writes the snapshot record at the very end); the orphaned data it had uploaded was cleaned up with `restic prune` afterward.
- `/srv/docker/immich/backups` (~1.9 GB): not attempted tonight at all, given the Nextcloud-files run had already shown the wall firmly at a smaller size.

**Conclusion:** the per-source split clearly helped -- everything under about 200 MB got through, sometimes needing one retry, which is why 6 of 8 succeeded tonight where the single bundled 2.5 GB run failed outright twice. The two remaining sources are simply too large to reliably clear the shared quota's remaining headroom tonight. This still points at the same fix.

**Update 2026-09-23 -- personal client ID working, 8 of 8 sources now off-site.**
A personal Google Cloud OAuth client is now in use, and the last two sources went through in one pass each with no rate-limit errors: `nextcloud-files` (736 MiB, 2m16s, snapshot `e60c6bac`) and `immich-db` (2.26 GiB, 7m42s, snapshot `eee5fbe5`). Verified beyond the exit codes: `restic check --read-data-subset=5%` clean, and one Nextcloud file restored from Drive and matched the live copy by SHA-256. The nightly job is still NOT scheduled (see the Testing-mode item below).

What the two days of `unauthorized_client` / `invalid_client` actually were (none of it was Google propagation delay, despite the working theory at the time):
- **Every failed `rclone authorize` was a copy/paste problem.** The long command was split at line wraps, so PowerShell ran `rclone authorize "drive"` alone -- which silently uses rclone's *default* shared client -- and then tried to run the client ID and secret as separate commands. A token from the default client fails with `unauthorized_client` when paired with a custom client ID in `rclone.conf`. Fix: set `$id` / `$secret` as PowerShell variables on separate short lines, then `rclone authorize "drive" $id $secret`. The proof it used the custom client: Google shows "Google hasn't verified this app" (the default client is verified, so it never does).
- **`rclone config update gdrive token '...'` writes the token and then hangs** waiting for interactive input. Kill it after the write; check with `rclone about gdrive:`. Prefer `--non-interactive`.
- The client ID itself was valid the whole time (probed against Google's auth endpoint, with rclone's default ID and a made-up ID as controls).

**Still open before the nightly job goes live:**
- **The OAuth app is in Testing mode, so its refresh token expires after 7 days** (this one issued 2026-09-23, dies about 2026-09-30). Publishing to "In production" is blocked: the Audience page says "To publish your app, you must complete your configuration on the Branding page" without naming the field. Suspect: the Developer contact email on the Branding page (not saved / empty) -- unconfirmed. After publishing, re-run `rclone authorize` for a fresh token and revoke the old grant at myaccount.google.com/permissions.
- Create the Kuma push monitor and install the crontab line -- only after the token is durable, so the job cannot start failing nightly a week from now.

**Costs / sacrifices:**
1. Off-site restore depends on the repository password. Losing all three copies (this file, Apple Keychain, paper) means the Google Drive copy is unrecoverable, by design (that is what encryption means).
2. Backing up SSH private keys off-site, even encrypted, is a real increase in what a compromise of the password would expose. Judged acceptable given the password's three-way custody.
3. Google Drive is a personal account, not a dedicated backup service; if Jay's Google account were ever compromised or lost, so is this copy (mitigated only by the encryption).
4. Scope is Jay's data only for now (B7); Mafe's is not protected by this yet.

**Revisit if:** the free tier is outgrown; Mafe's data is added (B7); the Google account itself needs its own recovery plan; or a dedicated off-site provider becomes worth the cost.

---

## 3. Open questions

| # | Question | Notes |
|---|---|---|
| B-Q1 | Mafe's data: same mechanism, later? | Deferred per B7. |
| B-Q2 | Second NAS drive for real redundancy? | Costs money; see `fleet-architecture.md` §NAS finding under D11. Deferred per B4. |
| B-Q3 | The media library and Plex's history (replaceable but painful) -- any backup at all? | Not addressed yet; B1's second tier. |
| B-Q4 | Restore drill: has anyone actually restored a full Vaultwarden vault from one of these snapshots, not just diffed files? | The test above proved file-level restore; a real Vaultwarden-app-level restore drill is different and not yet done. |
