# Cleaning `downloads/complete` safely

`/mnt/media/downloads/complete` accumulates over time, but most of what's in it
is **not** wasted space, and some of it is load-bearing. Deleting by folder name
or by "is it in the library?" alone will break seeding.

## Three categories — check all three before deleting anything

| Category | How to identify | Verdict |
|---|---|---|
| Hardlinked to library | `stat` link count > 1 | **Keep.** Same bytes as the library file — deleting frees nothing and breaks seeding |
| Actively seeding | referenced by a qBittorrent `.fastresume` | **Keep.** Deleting kills the torrent |
| Orphan | neither of the above | Deletable |

As of 2026-08-04 the split was 90.4 GB hardlinked, 44.9 GB seeding, 49 GB orphaned.
Only that last third was worth reclaiming.

## Finding what qBittorrent is seeding

Resume data lives in `/srv/docker/qbittorrent/qBittorrent/BT_backup/*.fastresume`
(bencoded). Extract `save_path` and `name` from each, join them, and translate the
container path to the host path:

```
/data/downloads/... -> /mnt/media/downloads/...
/data/...           -> /mnt/media/...
```

A file is protected if its path equals, or sits beneath, any of those.

Do **not** rely on `lsof` — an idle seeding torrent holds no open file handle, so
it looks free when it isn't.

## Checking the library counterpart

Match by **name**, not size. A size match is meaningless: during this cleanup a
Kings of Leon album "matched" a Howard Zinn audiobook because both were the same
number of bytes.

Also confirm the folder name Sonarr actually uses — releases named
`Hajime.no.Ippo.*` live under `/mnt/media/tv/Fighting Spirit/`, so searching for
"Ippo" returns nothing and looks like a missing file.

## What went wrong on 2026-08-04

12 folders were deleted after verifying their content existed in the library.
That check was correct but insufficient — **9 of them were active torrents**.
The library copies were separate files, so nothing was lost from the media
library, but those 9 torrents now error with missing files.

The `df` reading afterwards also appeared unchanged; 49 GB against a 24 TB volume
is below the display granularity. Verify with `os.statvfs` or by measuring the
directory, not `df -h`.

## Procedure

1. Build the protected set from `BT_backup/*.fastresume`
2. Walk the tree, classify every file by link count / protection / mtime
3. Exclude anything modified in the last hour (may be mid-processing)
4. Set aside obfuscated names (32-char hex, `.7z` staging files) for manual review
   — "no library match" is not proof of redundancy when the name is meaningless
5. Write a manifest of the deletion list before deleting
6. Delete, then verify by directory size rather than `df`
