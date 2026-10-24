# Plex learns about new media from the *arrs (issue #70)

Plex runs on r9 and reaches media over NFS, so its inotify "update automatically" never fires for files the
*arrs write on production. The *arrs tell Plex directly instead.

## Setup (Sonarr id 2, Radarr id 2, Lidarr id 1: connection "Plex (r9)")

- Connect -> Plex Media Server, host 192.168.0.22:32400, no SSL, Update Library on.
- **Path mapping `/data` -> `/data/media`.** The *arrs see `/data/movies`; Plex sees `/data/media/movies`. Without
  it Plex cannot match the folder and the targeted scan does nothing.
- Triggers: On Import, **plus** On Upgrade, On Rename, On (File) Delete and Delete For Upgrade on Radarr and Sonarr
  (added 2026-10-24). On Import alone left Plex showing the OLD file's size/codec after a replace-import.
  Lidarr is still On Import only (see #82 for its slow rescan).
- The Plex token lives only in each *arr's own DB.

## What a good import looks like (timed 2026-10-24, Hubie Halloween, Usenet via SABnzbd)

Radarr import 14:47:03 -> Plex `GET /library/sections/1/refresh?path=/data/media/movies/<title>` at 14:47:03.590
-> item present, addedAt the same second. Plex scans only that folder, not the library.

## Gotchas

- A replace-import (e.g. a manual grab over an existing file) is not a "download" in Radarr's eyes. Without
  the On Upgrade trigger Plex keeps the old size/codec; fix with a section refresh on the path, then
  `PUT /library/metadata/<ratingKey>/analyze` (analyze is what updates audioCodec).
- `upgradeAllowed=false` stops grabbing, not importing: a manual grab still replaces the file.
- Check a grab's language before it imports. "German.DL" releases carry German first.
