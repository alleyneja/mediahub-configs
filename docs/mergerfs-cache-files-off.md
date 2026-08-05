# mergerfs — `cache.files=off` (the 16x write fix)

## 2026-08-04 — the pool was writing at 13% of the NAS's real speed

**Symptom:** everything that wrote to `/mnt/media` was slow. SABnzbd downloads
sawtoothed and collapsed; Sonarr imports crawled; a 543 MB episode took 42
seconds to copy. This had been true since the pool was first mounted and was
misdiagnosed repeatedly as a network, gateway, or NAS problem.

**Cause:** the mergerfs mount used `cache.files=partial`, present since the very
first commit (599001a, 2026-05-10) and never revisited. It was not chosen to fix
anything — it was simply what got written down.

Measured under real load, each variant against a direct-to-NAS control taken at
the same moment (the control matters — NAS throughput swings with Plex playback):

| Setting | pool write | direct | ratio |
|---|---|---|---|
| `cache.files=partial` + `dropcacheonclose` (old) | 4.4 MB/s | 33.3 | **13%** |
| `partial`, no dropcacheonclose | 3.5 | 16.8 | 21% |
| `partial` + `cache.writeback` | 9.2 | 25.4 | 36% |
| `full` + `cache.writeback` | 14.5 | 39.1 | 37% |
| `auto-full` + `cache.writeback` | 10.4 | 26.4 | 39% |
| **`cache.files=off`** | **36.0** | 30.2 | **119%** |

`off` uses direct I/O, skipping the double-caching that FUSE otherwise does. It
matches or beats writing straight to the NAS.

**Reads are unaffected** — 110 MB/s through the pool both before and after, so
Plex playback is unchanged. This was verified before applying.

## Applied

```
# /etc/fstab
/mnt/internal:/mnt/nas /mnt/media fuse.mergerfs defaults,allow_other,use_ino,\
category.create=mfs,nonempty,_netdev,nofail,x-systemd.after=mnt-nas.mount,cache.files=off 0 0
```

`cache.writeback` and `dropcacheonclose` were dropped — both only apply when
file caching is on.

Requires stopping every container that mounts `/mnt/media` (21 of them), then
`umount /mnt/media && mount /mnt/media`. An idle Samba session may also hold the
mount; `fuser -mv /mnt/media` finds it, and killing an idle `smbd` is safe since
clients reconnect.

**Result, measured with all 21 containers running:**

| | before | after |
|---|---|---|
| pool write | 4.4 MB/s | **73.0 MB/s** |
| pool read | 110 MB/s | 110 MB/s |
| import backlog | 32 stuck for hours | cleared in ~5 min |

## Why this mattered so much

One slow number produced a cascade of misleading symptoms:

- SAB wrote in-progress downloads into the pool → writes crawled → **this was
  the "sawtooth"**, not the SBG8300 gateway and not Newshosting throttling.
- Moving downloads to the NVMe fixed the sawtooth but put them on a different
  device from the library, which **broke hardlinks** — see
  `docs/sabnzbd-incomplete-on-nvme.md` and commit e2a1244.
- Imports then became physical copies at ~2 MB/s.

Always measure the union mount itself, not just the underlying branches.
Benchmarking `/mnt/nas` and `/mnt/internal` directly hides this entirely.

## Benchmarking notes

Two earlier attempts produced numbers that did not survive contact with
production:

- A separate idle test mount reads fast because nothing else is using that FUSE
  instance. Not representative.
- Benchmarking with containers stopped is not representative either.

Always: real load, and a direct-to-NAS control measured in the same window.

## Known consequence: no `mmap()` on the pool

`cache.files=off` means FUSE `direct_io`, and `direct_io` does not support
`mmap()` — it returns `ENODEV` ("No such device"). This broke every qBittorrent
torrent within hours of the change, since libtorrent 2.x memory-maps by default.
See `docs/qbittorrent-posix-disk-io.md`. Check any new service that touches
`/mnt/media` for mmap use.
