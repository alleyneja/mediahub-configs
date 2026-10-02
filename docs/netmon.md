# Internet connection monitor (netmon)

Added 2026-10-02. Remote Plex users reported buffering, and a one-off speedtest at midday showed 15.9 Mbps up while
the same line tested 36-42 Mbps at 3 AM. One test can't separate "slow line" from "busy line" from "bad route to
that server", so this logs the connection continuously.

## What it records (`scripts/netmon.py`, cron every 30 min on production)
**Every run, no load on the line**
- Idle latency, jitter and packet loss to the gateway (192.168.0.1), 1.1.1.1 and 8.8.8.8 (20 pings each).
- What Plex is serving, read from r9 over SSH: sessions, remote sessions, transcodes, **burned-in subtitles**, WAN kbps.
- What this host's downloaders are doing (gluetun/qBittorrent and SABnzbd rx/tx kbps), so a slow test can be blamed on them.

**Speed tests, which saturate the line**
- Upload test to two fixed servers on different networks (6030 fdcservers Ashburn, 70055 Brightspeed Charlottesville),
  about every 3 h, ~100 MB each. Download is added about every 11 h (~1 GB). Roughly 3-5 GB/day in total.
- A continuous ping to 1.1.1.1 runs during each test, so **latency under load (bufferbloat)** is recorded next to the
  idle figure. The SBG8300 has no queue management (see the SABnzbd burst note in `project_sabnzbd_burst_downloads`).
- **Never runs while a remote Plex stream is playing**, nor when Plex can't be read. The row is logged with
  `skip_reason` (`remote_streams` / `plex_unreadable`) and it retries 30 min later. Consequence: evening peak data will
  have gaps whenever someone is watching; the idle-latency probes still run then.

## Where the data is
`~/logs/netmon/netmon.csv` (one row per run), `netmon.jsonl` (raw speedtest JSON incl. public IP and per-session Plex
detail), `state.json` (when the last upload/download tests ran), `cron.log`. **Outside the repo on purpose**: the repo
is public and these hold the public IP.

## Reading it
`scripts/netmon-report.py [--days N]`: speed min/median/max per server, upload by hour of day, idle latency and loss,
bufferbloat per test, Plex activity next to each upload result, and how many tests were skipped for streams.

## Not collected (could be added)
- DOCSIS signal levels, SNR and uncorrectable errors from the SBG8300: the status pages need a login over HTTPS and the
  modem address (192.168.100.1) isn't reachable from the LAN. Worth adding if the data points at the line itself.
- Evening upload while people are streaming. Skipped by design; a lower-rate probe could be allowed if that gap matters.

## Tuning
`UPLOAD_EVERY_S`, `DOWNLOAD_EVERY_S`, `PINNED_SERVER`, `ALT_SERVER` at the top of `netmon.py`. Remove the cron line to stop it.
