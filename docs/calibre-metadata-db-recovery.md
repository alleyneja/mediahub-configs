# Calibre — corrupt `metadata.db` recovery

Calibre stores its library index in `metadata.db` at the root of the library
directory (`/mnt/media/ebooks/calibre-library`), not in this repo. This file
records the recovery so it can be repeated, and the gap that let it happen.

## 2026-08-02 — truncated `metadata.db` broke Calibre-Web login

**Symptom:** logging into Calibre-Web via Authentik failed. Authentik itself was
healthy, which made it look like an SSO problem.

**What was actually happening:** Calibre-Web bounces every route to
`/admin/dbconfig` when it cannot open the library database, so the OIDC flow
never started:

```
GET https://calibre-web.lan/login/generic  ->  302  ->  /admin/dbconfig
```

The logs showed `cps.calibre_init` and `cps.db` looping on
`(sqlite3.DatabaseError) database disk image is malformed`. Authentik's last
successful login was 2026-07-27 17:11 UTC; everything after that was Uptime-Kuma
health checks only.

**Cause:** `metadata.db` was truncated. Its SQLite header declared 132 pages
(540,672 bytes) but only 122 pages (499,712 bytes) were on disk — the last
40,960 bytes were missing. Modified 2026-07-27 22:50, during the house-move
reconnection, when `/mnt/nas` failed to mount at boot because the network was
unreachable. Same truncated-write signature as the July Nextcloud incident.

Only one copy existed, on the `/mnt/internal` mergerfs branch. **There was no
backup.**

## Recovery

The book files themselves were never at risk — only the index. Padding the file
back to its declared length with zeros made most of it readable again; only 3 of
38 tables were damaged (`books`, `comments`, `books_tags_link`).

1. Pad a *copy* to 540,672 bytes so SQLite will open it. Never work on the original.
2. Copy every table row-by-row into a fresh database (a bad page then costs
   individual rows, not the whole table). Recreate FTS virtual tables rather than
   copying their shadow tables.
3. Rebuild the 17 destroyed `books` rows. Calibre encodes the book id in each
   folder name (`Title (id)`), so title, author, path and format were all
   recoverable from disk plus the intact link tables.
4. Recreate indexes, triggers and views last; restore `user_version`.

Result: `PRAGMA integrity_check` = `ok`, all 52 books present, tags, ratings,
series and comments preserved. Books 14 and 61 have no format row — pre-existing
cover-only ghost entries, unrelated to the corruption.

The corrupt original is kept at
`/mnt/media/ebooks/calibre-library/metadata.db.corrupt-20260802`.

## Follow-ups

- **`metadata.db` has no backup.** It is the single point of failure for the
  whole library; the ebooks survive without it but all metadata does not.
  Worth a periodic `VACUUM INTO` snapshot.
- The mergerfs pool will happily accept writes while `/mnt/nas` is missing, so a
  boot-time NFS failure can produce partial files with no error. See also the
  `nofail` + `x-systemd.after=mnt-nas.mount` options in `/etc/fstab`.
