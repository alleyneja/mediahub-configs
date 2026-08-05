# qBittorrent — POSIX disk I/O (fallout from `cache.files=off`)

## 2026-08-04 — every torrent errored a few hours after the mergerfs change

**Symptom:** newly grabbed shows went straight to `errored` in qBittorrent and
never downloaded a byte. Nothing was downloading at all — 111 torrents were
stalled, errored, or missing files. The VPN was suspected first; it was fine.

**Cause:** `cache.files=off` (commit 1e0eedb, applied ~noon the same day) makes
mergerfs use FUSE `direct_io`, and **`direct_io` does not support `mmap()`**:

```
/mnt/media/downloads/incomplete   mmap FAIL: [Errno 19] No such device
/mnt/internal                     mmap OK
/mnt/nas                          mmap OK
```

qBittorrent 5.1.4 ships libtorrent 2.0.11, whose default disk backend on 64-bit
Linux is `mmap_disk_io` — every read and write is memory-mapped. qBittorrent's
`/data` is a bind of `/mnt/media`, and its temp path is
`/data/downloads/incomplete`, so both live on the pool.

Result: libtorrent mmaps → mergerfs returns `ENODEV` → the torrent errors.

```
Arthur 1996 Seasons 01 to 05 ... file_mmap (/data/downloads/incomplete/tv/...)
  error: No such device
```

First error in the qBittorrent log was 12:16:47, immediately after the mergerfs
remount. Nothing before it.

Error volume at the time of diagnosis (10.5 hours of log):

| Errors | Torrents | Class |
|---|---|---|
| 6,746 | 49 | `file_mmap` → No such device |
| 726 | 8 | `file_open` → Permission denied (on 0777 `jay:jay` files) |
| 6 | 6 | fast resume "mismatching file size" — unrelated, see below |

The `Permission denied` cases were the same root cause, not a permissions
problem — the files were world-writable and owned correctly.

## Applied

qBittorrent → Advanced → Disk IO type → **POSIX-compliant**, i.e. via the API:

```bash
curl -b cookies -X POST "http://localhost:8082/api/v2/app/setPreferences" \
  --data-urlencode 'json={"disk_io_type":2}'
docker restart qbittorrent
```

`disk_io_type` values are `0` = Default (mmap on 64-bit), `1` = Memory mapped
files, `2` = POSIX-compliant. libtorrent's `posix_disk_io` uses plain
`pread`/`pwrite` and works fine over FUSE. Both backends are compiled into the
LinuxServer image (`mmap_disk_io_constructor` and `posix_disk_io_constructor`
are both present in the binary).

This was chosen over reverting `cache.files=off` so the pool keeps its 73 MB/s
write speed — see `docs/mergerfs-cache-files-off.md`.

**Result, one minute after restart:**

| | before | after |
|---|---|---|
| `file_mmap` ENODEV | 6,746 | **0** |
| `file_open` EACCES | 726 | **0** |
| downloading | 0 | 3 (the Arthur grabs, with peers) |

## Not fixed by this

15 torrents remain in `missingFiles` with `fast resume rejected … mismatching
file size`. These are the casualties of the same day's `downloads/complete`
cleanup, already known — see `docs/cleaning-downloads-complete.md`. They are
mostly audiobooks and music. Six were visible before the restart; the other
nine only surfaced once POSIX I/O let qBittorrent actually stat the paths.

## The general rule

**Anything on `/mnt/media` that uses `mmap()` will fail with `ENODEV`.**

qBittorrent was not the only casualty — the Calibre stack was down the same way
for ~11 hours before anyone noticed. See `docs/calibre-library-on-ext4.md`.

`cache.files=off` is a storage-layer change with application-layer consequences.
When adding or upgrading a service that touches the pool, check whether it
memory-maps files — SQLite in some modes, some media scanners, and libtorrent
2.x all do. A quick probe:

```python
import mmap, os
with open("/mnt/media/.probe", "wb") as f: f.write(b"x"*4096)
with open("/mnt/media/.probe", "r+b") as f: mmap.mmap(f.fileno(), 4096)
```
