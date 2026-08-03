# Unpackerr — silent two-week outage

## 2026-07-19 to 2026-08-02 — stopped during the NAS incident, never restarted

**Symptom:** archives stopped being extracted. Nothing alerted, because nothing
had crashed — the container was simply absent.

**Cause:** unpackerr exited 2026-07-19 14:56 UTC with code 137 during the NAS
nfsd thread-starvation work. Not an OOM (`OOMKilled: false`) — the log ends with
`Caught Signal: terminated`, so it received SIGTERM, took longer than Docker's
10-second grace period to stop because it was mid-extraction on a 9 GB archive,
and was then SIGKILLed.

It stayed down through every reboot afterwards, including 2026-08-02. That is
`restart: unless-stopped` behaving exactly as documented: an *explicit* stop is
remembered, and Docker deliberately does not bring the container back. A crash
would have been restarted; a clean stop is not.

**Fix:** `docker start unpackerr`. Confirmed polling Sonarr, Radarr, Lidarr and
both Readarr forks again.

## Follow-ups

- Unpackerr only extracts what is currently in an \*arr queue. Two folders of
  archives (54 `.rar` files) accumulated while it was down and have aged out of
  the queues — they need manual extraction:
  - `/mnt/media/downloads/complete/Think.and.Grow.Rich-Napoleon.Hill.Erik.Synnestvedt`
  - `/mnt/media/downloads/complete/tv/Goosebumps.S01.WEBRip.EAC3.5.1.1080p.x265-iVy`
- Uptime-Kuma does not monitor unpackerr, so a stopped container is invisible.
  It has no HTTP endpoint by default (`Webserver Disabled` in its config), but a
  Docker-type monitor would catch this.
- Worth checking `docker ps -a --filter status=exited` after any incident that
  involved stopping containers.
