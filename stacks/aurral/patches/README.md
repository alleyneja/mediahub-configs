# Aurral local patches

Bind-mounted over files inside the pinned image (`ghcr.io/lklynet/aurral:3.1.0`).
Each patch keeps `<file>.orig` (pristine copy from the image) and `<file>.diff`.
**Re-diff against the new image on any version bump**, and drop the patch if upstream fixed it.
After editing a bind-mounted file on the host, `docker compose restart aurral` (not `up -d`).

## backgroundProcessSupervisor.ts  -- watchdog 120 s -> 1800 s

- **Symptom (Nov 23-24 2026, after the 3.1.0 upgrade):** library scans never finish. The scan
  worker is SIGKILLed ("library worker stopped responding") every ~2 minutes, ~20 kills in 3 h,
  1,000+ scan runs stuck in `running` in `library_scan_runs`, constant "database is locked"
  errors, and Aurral download-job rows stuck on `downloading` after Lidarr had already imported.
- **Cause (upstream bug, not our NFS/mergerfs):** during a scan the worker is CPU-bound (~90 % of a
  core, RSS climbing to ~1 GB) while `reconcileLibraryAlbums` holds the SQLite write lock for tens
  of seconds. The worker's event loop cannot send heartbeats, and the supervisor kills any worker
  silent for `UNRESPONSIVE_MS` = 120000. Killed scans restart from scratch, so they never complete.
  Same bug reported upstream: lklynet/aurral#2139 (open, fixed only on nightly if at all).
- **Patch:** `UNRESPONSIVE_MS` 120000 -> 1800000 so a scan can run to completion. Pre-upgrade (2.x)
  full scans of /data/music took avg 40 s, max 957 s.
- **Trade-off:** a genuinely hung worker now takes up to 30 min to be restarted.
- **Remove when:** upstream ships a release that fixes #2139 / makes the scan yield.
- Tracked in alleyneja/mediahub-issues #89 (scan) and #90 (upgrade record).
