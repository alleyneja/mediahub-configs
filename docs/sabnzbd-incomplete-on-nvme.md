# SABnzbd — incomplete downloads moved off the mergerfs pool

## 2026-08-03 — sporadic "sawtooth" download speed

**Symptom:** SAB throughput repeatedly ramped up then collapsed —
`18.8 M → 13.4 → 9.2 → 5.4 → 883 K → 142 K` — over and over.

Superficially this looked like the July 2026 gateway problem (SBG8300 NAT/buffer
exhaustion, mitigated then by dropping Newshosting connections 8 → 4). That
setting was still applied and was **not** the issue this time. Measured during a
collapse:

```
segments retransmitted : 0.63/s
SACK reordering        : 0.10/s
NFS rpc retrans        : 14 of 11,029,572 calls
```

The network was clean. The bottleneck was **storage**.

**Cause:** `download_dir` was `/data/downloads/incomplete`, i.e. inside the
mergerfs pool. mergerfs uses `category.create=mfs` (most free space), and the
NAS branch always has the most free space, so **every incomplete write went to
the NAS over NFS** — millions of small random article writes, competing with
Plex reads, qBittorrent and unpackerr on one gigabit link.

Measured write throughput:

| Target | Device | Speed |
|---|---|---|
| `/` (`/srv`) | KIOXIA 2 TB NVMe | **869 MB/s** |
| `/mnt/internal` | WD120EAGZ 12 TB HDD | 224 MB/s |
| `/mnt/nas` | NFS to UGREEN NAS | 41–73 MB/s (varies with contention) |

Note `/mnt/internal` is a *spinning disk*, not the NVMe — easy to assume
otherwise given the name.

## Fix

Bind-mount NVMe scratch space into the container and point `download_dir` at it.
Completed files still land on the pool, so only the scratch path changed.

```yaml
volumes:
  - /srv/downloads/incomplete:/incomplete
```

```ini
download_dir = /incomplete            # was /data/downloads/incomplete
complete_dir = /data/downloads/complete   # unchanged
```

`sabnzbd.ini` is not in this repo; the setting is recorded here.

**Result**, sustained 12-sample measurement:

| | Before (NAS) | After (NVMe) |
|---|---|---|
| mean | ~12 MB/s | **28.2 MB/s** |
| floor | 2.8 MB/s | **24.6 MB/s** |
| peak | 19.4 MB/s | 32.6 MB/s |
| std dev | wild | 2.5 |

Confirmed by growth during the window: NVMe +2,865 MB, NAS +13 MB.

qBittorrent was deliberately left on the pool — torrents seed from the completed
file and Sonarr hardlinks into the library, which only works within one
filesystem.

## Migrating safely — two traps

Changing `download_dir` while jobs exist **will destroy queue state** unless the
job folders are moved first. Both traps below were hit in practice.

1. **The download queue.** On startup SAB looks for each job's folder under the
   current `download_dir`. Folders missing → it rewrites `queue10.sab` as empty
   and the queue is gone. Reverting the path does *not* undo this, because the
   file is already overwritten.

2. **The post-processing queue.** Jobs in `postproc2.sab` store **absolute
   paths**. They are not in the download queue, so they are easy to miss when
   enumerating what to move — and they are orphaned even if you *do* move their
   folders, because the stored path still points at the old root.

**Recovery for both:** SAB keeps per-job metadata in `__ADMIN__` inside each job
folder. `mode=restart_repair` rescans `download_dir` and rebuilds the queue from
it. This recovered ~200 jobs and 220 GB across two separate incidents here.

**Correct procedure:**

```
1. pause queue, stop the container
2. cp -a /srv/docker/sabnzbd/admin  /srv/docker/sabnzbd/admin.BACKUP-<date>
3. move BOTH download-queue and post-processing job folders to the new path
4. repoint download_dir, start, verify job counts BEFORE resuming
5. if post-processing jobs were orphaned, run mode=restart_repair
```

Step 2 is the one that matters — `admin/` holds `queue10.sab`, and without it a
mistake is unrecoverable except via `__ADMIN__` rebuild.

## Follow-up

~200 GB of orphaned incomplete folders had accumulated on the NAS from
abandoned jobs. Repair re-adopted most of them; anything genuinely dead should be
pruned from `/mnt/nas/downloads/incomplete` rather than left to consume space.

---

## CORRECTION (2026-08-04)

The diagnosis above is **incomplete**. The slow pool writes were not primarily
NFS contention — they were caused by mergerfs running with `cache.files=partial`,
which limited pool writes to ~4 MB/s against a NAS capable of ~50-70 MB/s.
Fixing that one mount option took pool writes to 73 MB/s under full load. See
`docs/mergerfs-cache-files-off.md`.

Two further corrections:

- Moving **completed** downloads to the NVMe (attempted 2026-08-04) was a
  mistake. It put them on a different device from the library, which broke
  hardlink imports and dropped them to ~2 MB/s — exactly what commit e2a1244
  warned about in May. Reverted the same day.
- Only **in-progress** downloads belong on the NVMe. Current layout is documented
  in `docs/download-storage-layout.md`.

The measurements in this file were also taken in unrepresentative conditions
(idle test mount, or with containers stopped) and did not hold under real load.
