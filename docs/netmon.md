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
- **Never runs while any video stream is playing, LAN included** (changed 2026-10-03), nor when Plex can't be read. The
  gateway is also the LAN switch and Wi-Fi AP, and on 2026-10-02 22:00 three buffering reports on a LAN Fire TV landed inside
  our own scheduled test, which only checked for *remote* streams. Music (Plexamp) doesn't block. The row is logged with
  `skip_reason` (`streams_active` / `plex_unreadable`) and it retries 30 min later. Consequence: evening peak data will have
  gaps whenever someone is watching; the idle-latency probes still run then. `test_start`/`test_end` are logged.

## Buffering events and the 1-minute sampler (added 2026-10-03)
- Every 30-min run copies new `client reported state buffering` lines from r9's Plex logs into `buffering-events.csv`
  (`startup=1` when within 3 s of the start). Plex rotates its logs in ~6 h, so nothing is missed.
- `netmon.py --sample` (cron, every minute) writes `minute.csv`: qBittorrent (gluetun tun0) and SABnzbd rx/tx kbps as the average
  since the last sample, plus 4 pings to 1.1.1.1. `netmon-report.py` joins the two: for each mid-play buffering event it shows
  what the line was doing that minute, and compares "share of events during heavy qBittorrent upload" with "share of all
  minutes with heavy upload" (the control). Needs a few days of data before it means anything.

## Change log (things we changed because of what this showed)
- **2026-10-03 ~10:15 CDT: qBittorrent global upload limit 0 (unlimited) -> 1,800,000 B/s (~14.4 Mbps).** It had no limit and
  no seed ratio/time limit, and 92 torrents seeding; it sometimes used 25-38 Mbps of the ~38 Mbps uplink (the 06:00 test got
  6.2 Mbps up while qBittorrent sent 38). Jay: Plex users take priority. Revert: set `up_limit` to 0. Connection counts were
  deliberately left alone so the before/after stays clean. Not yet shown to fix any buffering: see the evidence notes below.

## Evidence so far (2026-10-03, to be re-read after a few days of data)
- Supports upload contention: measured upload dips line up with heavy qBittorrent seeding.
- Does not explain: Josef's remote stall on 2026-10-02 ~08:15 (qBittorrent idle; SABnzbd downloading at 87 Mbps, ~2% TCP
  retransmits on his path); and ~15 mid-play stalls on two LAN Fire TVs direct-playing 6-7 Mbps HEVC files overnight
  (qBittorrent sending only 1-7 Mbps, no speed test running). r9's read path was healthy when checked (cold reads of the file at
  ~100 MB/s, 1 NFS retransmit in 1.75e9 calls, ~4 ms read RTT), so remaining suspects for those are the Fire TV / its Wi-Fi link or
  the gateway's Wi-Fi/LAN handling.

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
