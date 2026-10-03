# Internet connection monitor (netmon)

Added 2026-10-02. Remote Plex users reported buffering, and a one-off speedtest at midday showed 15.9 Mbps up while
the same line tested 36-42 Mbps at 3 AM. One test can't separate "slow line" from "busy line" from "bad route to
that server", so this logs the connection continuously.

## What it records (`scripts/netmon.py`, cron every 30 min on production)
**Every run, no load on the line**
- Idle latency, jitter and packet loss to the gateway (192.168.0.1), 1.1.1.1 and 8.8.8.8 (20 pings each).
- What Plex is serving, read from r9 over SSH: sessions, remote sessions, transcodes, **burned-in subtitles**, WAN kbps.
- What this host's downloaders are doing (gluetun/qBittorrent and SABnzbd rx/tx kbps), so a slow test can be blamed on them.
- **Modem signal (added 2026-10-03):** logs into the SBG8300 and records downstream SNR/power per run (min/avg/max over 31
  channels), OFDM RxMER, upstream power, how many upstream channels fell back below 64QAM, and the correctable /
  uncorrectable codeword **delta since the previous run** (a counter that goes down is logged as `m_reset`, i.e. the modem
  rebooted or resynced). Per-channel detail goes to `netmon.jsonl`.

**Speed tests, which saturate the line**
- Upload test to two fixed servers on different networks (6030 fdcservers Ashburn, 70055 Brightspeed Charlottesville),
  about every 3 h, ~100 MB each. Download is added about every 11 h (~1 GB). Roughly 3-5 GB/day in total.
- A continuous ping to 1.1.1.1 runs during each test, so **latency under load (bufferbloat)** is recorded next to the
  idle figure. The SBG8300 has no queue management (see the SABnzbd burst note in `project_sabnzbd_burst_downloads`).
- **Never runs while a remote Plex stream is playing**, nor when Plex can't be read. The row is logged with
  `skip_reason` (`remote_streams` / `plex_unreadable`) and it retries 30 min later. Consequence: evening peak data will
  have gaps whenever someone is watching; the idle-latency probes still run then.

## Modem credentials
`scripts/modem.env` (gitignored, mode 600): `MODEM_USER` is the email registered in the SURFboard app, `MODEM_PASS` the
gateway password. The password was set temporarily (2026-10-02) with the plan to change it after about a
week of data collection: **when you change it, edit `modem.env` too.** If the gateway rejects the login, netmon logs
`credentials rejected` and does NOT retry until `modem.env` is edited (repeated bad logins can lock the gateway out).
Login mechanics: plain `curl`/urllib works (the page's JS encryption is switched off); a JSON `PUT` to
`/actionHandler/ajaxSet_login.php`, then `wan.php` embeds the tables as `let channelData = {...}`. Sessions expire after a few
minutes, so each run logs in and out. Only one admin session exists: if someone is in the gateway UI while a run happens,
that run may fail or kick them out (logged as `modem_error`).

## Where the data is
`~/logs/netmon/netmon.csv` (one row per run), `netmon.jsonl` (raw speedtest JSON incl. public IP and per-session Plex
detail), `state.json` (when the last upload/download tests ran), `cron.log`. **Outside the repo on purpose**: the repo
is public and these hold the public IP.

## Reading it
`scripts/netmon-report.py [--days N]`: speed min/median/max per server, upload by hour of day, idle latency and loss,
bufferbloat per test, Plex activity next to each upload result, and how many tests were skipped for streams.

## Not collected (could be added)
- The modem's event log (`ajax_troubleshooting_logs.php`): returns 401 even when logged in, not solved.
- Evening upload while people are streaming. Skipped by design; a lower-rate probe could be allowed if that gap matters.

## Tuning
`UPLOAD_EVERY_S`, `DOWNLOAD_EVERY_S`, `PINNED_SERVER`, `ALT_SERVER` at the top of `netmon.py`. Remove the cron line to stop it.
