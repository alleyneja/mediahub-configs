# Download storage layout — where downloads live, and why

## Current layout (2026-08-04)

| Stage | Path (host) | Device | Why |
|---|---|---|---|
| In-progress | `/srv/downloads/incomplete` | NVMe | SAB writes thousands of small article fragments here; keeping them off the pool avoids FUSE overhead entirely |
| Finished | `/mnt/media/downloads/complete` | pool | **must** be the same device as the library so \*arr apps hardlink instead of copying |
| Library | `/mnt/media/{tv,movies,...}` | pool | — |

SABnzbd:

```ini
download_dir = /incomplete                  # bind: /srv/downloads/incomplete
complete_dir = /data/downloads/complete     # bind: /mnt/media
```

qBittorrent stays entirely on the pool — it seeds from the completed file, and
hardlinks mean the library and the torrent share one copy on disk.

## The rule that matters

**Finished downloads and the library must be on the same device.** Check with:

```sh
stat -c%d /mnt/media/downloads/complete
stat -c%d /mnt/media/tv          # must match
```

If they differ, every import becomes a physical copy. Commit e2a1244 (2026-05-10)
established this; it was accidentally undone on 2026-08-04 by moving finished
downloads to the NVMe, which dropped imports to ~2 MB/s until reverted.

In-progress downloads are exempt — nothing is ever hardlinked out of that folder,
so it is free to live on the fastest disk available.

## Why in-progress is on the NVMe

Before 2026-08-03 both stages were on the pool. With `cache.files=partial` the
pool wrote at 4 MB/s, so SAB's download throughput sawtoothed and collapsed. See
`docs/sabnzbd-incomplete-on-nvme.md`.

That root cause is now fixed (`docs/mergerfs-cache-files-off.md`), so in-progress
downloads *could* move back to the pool. Keeping them on the NVMe is still
preferable — small random writes are what FUSE handles worst — but it is no
longer load-bearing.

## Gotchas when changing these paths

Changing `download_dir` or `complete_dir` while jobs exist **destroys queue
state**:

- SAB rewrites `queue10.sab` as empty if job folders aren't at the new path.
  Reverting the setting does not undo it.
- Post-processing entries in `postproc2.sab` hold **absolute** paths and orphan
  separately — they are not in the download queue, so enumerating from the queue
  API misses them.

Recovery: every job folder contains `__ADMIN__`; `mode=restart_repair` rebuilds
the queue from it. This recovered ~200 jobs and 220 GB across two incidents.

Correct order: pause → stop → `cp -a admin/ admin.BACKUP-<date>` → move **both**
download-queue and post-processing folders → repoint → start → verify counts
before resuming.
