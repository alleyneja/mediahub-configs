# Plex on r9: missing content, and the Solo Leveling stall (2026-09-21)

Plex moved to mediahub-r9 (GPU server) on 2026-09-21. Shows appeared to lose
episodes (Oswald: Sonarr 52, Plex 18, then 0), whole albums and movies vanished,
and one show would not play on a PC. Three separate problems, written up so the
next person does not repeat the search. Nothing in this doc needs a secret.

## 1. Missing content: what actually happened

**Symptom.** After cutover, r9's Plex had about 6,700 fewer library items than
production's Plex had, across 50 of 171 shows plus most NAS-side music and some
movies. Every file was still on disk. Auto-scans then made it worse.

**Not the cause (ruled out with evidence).**
- The internal drive not being visible to r9. r9's mergerfs pool matched
  production file for file (9,500 TV, 1,275 movies), and 0% of the losses were on
  the internal drive. All 6,723 lost items were on the NAS branch.
- File changes (every recorded size matched disk), path prefix, filenames,
  mergerfs version or options, NFS errors or outages.

**Cause.** A NAS-side bulk permission rewrite at 16:14-16:16 NAS time (EDT, one
hour ahead of the servers) on 2026-09-20 touched about 14,000 files and 1,500
folders. For a while NFS clients saw about 6,800 media files as mode `000`,
unreadable by Plex's user. r9's fresh scan treated unreadable files as gone and
moved them to the trash. A forced rescan then purged them.

The NAS filesystem is btrfs mounted with UGREEN's `ugacl` option, so mode bits
over NFS are a translation of an ACL layer and not a plain chmod. The NAS's own
`find -perm 000` found nothing, and 3,875 podcast files that production saw as
`000` returned to normal on their own.

**Why production's Plex hid it.** Its scanner skips folders whose modified time
has not changed, and a permission change does not bump a folder's mtime. Those
files were unplayable for everyone from 15:15 CDT on Sep 20, and nobody noticed.

**Actor: never identified.** Ruled out for the burst window: the NAS journal,
syslog and cron log were empty from 16:08 to 16:17; the only NAS sudo commands
that evening (read-only recon, then an /etc/exports edit to add r9 at 16:17:12)
fall outside it; neither production nor r9 journals show a chmod; no container
logged permission activity. Leading hypothesis: a UGOS web-UI action (shared
folder permissions or media settings), which does not log to the system journal.
The owner recalls being in the UI around then. This is a hypothesis, not proof.

**Fix applied.**
1. Restored production's Plex `library.db` and `library.blobs.db` onto r9
   (Plex stays on r9 with the GPU). r9's previous databases are kept in
   `/srv/docker/plex/db-swap-backup-20260921/`.
2. `chmod 644` on the 6,840 mode-000 files in the three Plex libraries. The list
   is in `~/logs/mode000-plexlibs-20260921.txt` on production. Files were 777
   before on the NAS, so this really changed them.
3. Verified: 0 files unreadable by Plex's user; per-show episode counts identical
   to production across all 171 shows; a forced scan of one show afterwards kept
   all of its episodes (it deleted them before the permission fix).
4. Auto-scans turned back on (15-minute schedule plus file-change scans), and
   `autoEmptyTrash` turned **off** so a repeat lands in the trash where it can be
   restored, not permanently deleted.

**If content goes missing again, check these first (about a minute):**
```
find /mnt/nas/music /mnt/nas/movies /mnt/nas/tv -type f -perm 000 | wc -l
docker exec -u abc plex find /data/media -type f ! -readable | wc -l   # run on r9
```
Any non-zero result means permissions, not Plex. Test readability as Plex's user
from r9; permission bits alone are not enough (r9 saw some `777` files as
unreadable through a stale NFS attribute cache until a chmod refreshed them).

## 2. Plex keeps stale media info after Sonarr replaces a file

Replacing an episode with a different release under the same filename leaves Plex
holding the old codec, stream list and size. Playback then fails: the transcoder
is told to decode the old codec and map the old stream index against the new
file, and dies with "Invalid data found when processing input".

- Symptom in the log: transcoder args name a codec the file no longer has (for
  example `libdav1d` for a file that is now HEVC).
- Check: compare `media_parts.size` in the Plex DB with the file's size on disk.
- Fix: `PUT /library/metadata/<id>/refresh`, and if that only re-runs the metadata
  agent, `PUT /library/metadata/<id>/analyze`. Neither touches media files or
  watch history.
- The 15-minute auto-scan picked up most episodes on its own but missed two, so
  spot-check after a bulk replacement.

## 3. Solo Leveling would not play on the PC (audio-timing theory)

**Symptom.** PC plays one second, then buffers. The phone plays it fine.

**Ruled out.** CPU or GPU limits (the GPU transcode ran about 5x real time),
corrupt video output, the resume position (play from beginning failed the same
way), AV1 itself (another AV1 title transcoded to the same PC fine), Audio boost
(it does not change the stream; segments were byte-identical at 100 and 110).

**Finding.** All 25 files from the old release group had audio starting 1.000 s
after the video. Plex copies compatible stereo AAC as-is, so the first one-second
HLS segment carried video but zero audio packets, and audio first appeared in
segment 1. The PC client stalled after that. Working titles all had their audio
converted by Plex (AC3 or 5.1 HE-AAC to MP3), which pads the start.

**Status: the mechanism is reproduced, the cause of the browser stall is an
inference.** After the season was re-downloaded (a different release, HEVC video
with E-AC3 dual audio), Plex converts the audio and segment 0 carries audio. The
owner's PC playback result on the new file is the confirming test. Season 1 is
still the old release and is expected to stall the same way.

**If a file needs fixing in place:** shifting the audio timestamps earlier would
put it 1 s out of sync. A real fix pads 1 s of silence onto the audio, which
re-encodes the audio tracks (video untouched).

Note for release searches: Sonarr interactive search results sorted by seeders
hide Usenet results, which have no seeders. The delay profile already prefers
Usenet with no delay, so automatic grabs pick NZBs when available.

## Follow-ups
- Find out what changed the NAS permissions (check the UGOS system log around
  16:14 EDT on Sep 20), or add a monitor that alerts on new mode-000 files in the
  Plex libraries and tests readability as Plex's user from r9.
- Decide what to do about Solo Leveling Season 1.
- Consider a Plex-versus-Sonarr file-size drift check.
