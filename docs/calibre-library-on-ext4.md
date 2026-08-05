# Calibre — library moved off the mergerfs pool onto ext4

## 2026-08-04 — the whole Calibre stack was down for ~11 hours, silently

**Symptom:** none reported by a human. Found by auditing what else on
`/mnt/media` uses `mmap()` after qBittorrent broke the same way — see
`docs/qbittorrent-posix-disk-io.md`. All three Calibre containers had been
erroring since the mergerfs remount at noon:

```
calibre-web-automated   sqlite3.OperationalError: disk I/O error
                        [SQL: attach database '/calibre-library/metadata.db']
calibre-content-server  apsw.IOError: IOError: disk I/O error
bookshelf-ebooks        CalibreRootFolderCheck: Unable to connect to Calibre library [404]
```

**Cause:** `metadata.db` was the only database living on the mergerfs pool —
every other service keeps its DB on `/srv/docker` (ext4). It runs in **WAL
mode**, and WAL requires SQLite to `mmap()` its `-shm` file. `cache.files=off`
means the pool cannot `mmap()` at all. Reproduced on a scratch DB:

| location | `PRAGMA journal_mode=WAL` then write |
|---|---|
| `/mnt/media` (pool) | **disk I/O error** |
| `/mnt/internal` (ext4) | ok |
| `/mnt/nas` (NFS) | ok |

The data was never corrupt — an immutable-mode read returned all 53 books
before anything was touched. This was purely an access failure, unlike the
2026-08-02 truncation (`docs/calibre-metadata-db-recovery.md`).

## Why ext4 and not the NAS

The NAS has far more free space (17 TB vs 1.7 TB), but space was never the
constraint — the whole library is **155 MB**. The deciding factor is that
`metadata.db` is a live database, and `/mnt/nas` is NFS v3. SQLite over NFS has
unreliable locking, and this exact file was already truncated once on 2026-08-02
when `/mnt/nas` failed to mount during the house-move reconnection.

Book files are fine on a network mount — they are written once and read. A
half-written database is a different class of problem. Calibre requires
`metadata.db` to sit at the library root, so the books move with it.

## The library was split across both branches

mergerfs had scattered it, which is normal and invisible through the pool:

| branch | files | size |
|---|---|---|
| `/mnt/internal/ebooks/calibre-library` | 110 | 104 MB |
| `/mnt/nas/ebooks/calibre-library` | 22 | 51 MB (incl. `metadata.db`) |

Binding a single branch would therefore have hidden books. Verified **zero
file-level collisions** between branches before consolidating, and 110 + 22 =
132 matched the pooled view exactly.

## Applied

1. Backed up: full library tarball and a standalone `metadata.db` copy to
   `/srv/docker/_backups/calibre/`.
2. Stopped the three containers; confirmed `metadata.db-wal` was 0 bytes, so no
   transactions needed replaying.
3. `rsync` NAS branch → internal branch, excluding the stale `-shm`/`-wal`.
   Verified with `md5sum` on the database.
4. Verified integrity, WAL mode, and a concurrent reader **on ext4** before
   deleting anything.
5. Removed `/mnt/nas/ebooks/calibre-library` so the pool serves no stale copy.
6. Repointed the binds from the pool to the ext4 branch:

```yaml
# was: /mnt/media/ebooks/calibre-library
- /mnt/internal/ebooks/calibre-library:/calibre-library   # calibre-web-automated
- /mnt/internal/ebooks/calibre-library:/library           # calibre-content-server
- /mnt/internal/ebooks/calibre-library:/library           # bookshelf-ebooks
```

The library is still visible at `/mnt/media/ebooks/calibre-library` for
everything else, because the pool still includes the internal branch. Nothing
else changed.

**Verified after:** content server API reports 53 books; calibre-web redirects
to `/login` rather than `/admin/dbconfig` (the tell that it cannot open the
library); zero I/O errors across all three; `bookshelf-audiobooks`,
`rreading-glasses`, and `rreading-glasses-db` were left running untouched.

## Deployment gotcha: these stacks are Portainer-managed

`docker compose up -d` from this repo **fails** with name conflicts. These
containers are owned by Portainer stacks, and this repo is a mirror. The live
files are:

| stack | project name | live file |
|---|---|---|
| calibre-web-automated | `calibre-web-automated` | `portainer_data/_data/compose/19/docker-compose.yml` |
| calibre-content-server, bookshelf-* | `calibre-readarr` | `portainer_data/_data/compose/9/docker-compose.yml` |
| rreading-glasses, -db | `9` | same file as above |

Edit the live file *and* this repo, then recreate with the matching project
name, naming services explicitly so neighbours in the same file are not
disturbed:

```bash
P=/var/lib/docker/volumes/portainer_data/_data/compose
sudo docker compose -p calibre-readarr --env-file $P/9/stack.env \
  -f $P/9/docker-compose.yml up -d --no-deps calibre-content-server bookshelf-ebooks
```

Three services in `compose/9` carry the project label `calibre-readarr` and two
carry `9`. Do not run a bare `up -d` on that file — it will try to recreate all
five.

## The general rule, and one caveat

Databases belong on `/srv/docker` or a branch path — never *accessed through*
`/mnt/media`.

The caveat: `metadata.db` is still **visible** at
`/mnt/media/ebooks/calibre-library/metadata.db`, and always will be, because the
pool unions the internal branch. What changed is the path the containers use.
Anything that opens it through `/mnt/media` — a backup script, a manual
`sqlite3` session, a future container wired to the pool path — will still fail
with `disk I/O error`. Reach it at `/mnt/internal/ebooks/calibre-library/`.
