# 5,481 subtitle files with mode 000 — found via Bazarr, not caused by it

Found 2026-09-14 while investigating why Bazarr wasn't downloading subs for a specific
show. Written down because it's a second, unrelated bug that was hiding behind the first
one, and because the trend line means it could come back.

## What was found

57% of every subtitle file in the library (5,481 of 9,628, across 138 TV shows plus a
couple of movies) had file mode `000` — unreadable by anyone, including the owning user
(`jay`). Confirmed as a real filesystem permission, not a container/UID-mapping artifact,
by failing to `open()` one directly from the host shell, outside any container.

Bazarr's daily `series_full_scan_subtitles` / `movies_full_scan_subtitles` jobs throw a
`PermissionError(13)` for every one of these
(`subtitles/indexer/utils.py:122`, `open(subtitle_path, 'rb')`), and — critically —
Bazarr then reports those episodes as still **missing** subtitles even though a file
exists on disk. This is what made the original report ("Bazarr isn't downloading
anything") look worse than it was: some of what looked like a download failure was
actually Bazarr being unable to see subtitles that were already there.

## It was accelerating, not a one-time event

Checked file ctimes: 325 in May, 105 in June, 307 in July, 1,336 in August, 3,408 in the
first two weeks of September alone. That acceleration lines up with the storage
migration work from the same window — `mergerfs-cache-files-off.md`,
`sabnzbd-incomplete-on-nvme.md`, `portainer-to-repo-migration.md` are all Aug-Sep 2026.

**Root cause of *why* files end up at mode 000 was not confirmed.** The Bazarr
container's umask is a normal `0022`, so it isn't Bazarr's own write path creating these.
Most likely some bulk copy/migration operation during that storage work preserved or
introduced zero permissions on a subset of files, but this wasn't proven — worth
re-checking if the count climbs again.

## Fix applied

```bash
find /mnt/media -type f \( -iname "*.srt" -o -iname "*.ass" -o -iname "*.sub" -o -iname "*.vtt" \) -perm 000
```
piped to `xargs chmod 644`, then triggered Bazarr's `series_full_scan_subtitles` and
`movies_full_scan_subtitles` tasks via its API to re-index. Verified zero new
`PermissionError` entries afterward, and spot-checked Suits/Fallout/Game of Thrones back
to 0 missing subtitles.

## How to check if this comes back

If Bazarr's missing-subtitle counts look wrong again, or "unable to index external
subtitles" errors reappear in `/config/log/bazarr.log`, run the `find ... -perm 000`
command above before assuming it's a provider or config problem.
