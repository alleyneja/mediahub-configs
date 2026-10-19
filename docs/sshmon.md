# SSH responsiveness monitor (sshmon)

Added 2026-10-18. From 2026-10-16/17 Claude/SSH tabs from the gaming PC (Windows PowerShell, Wi-Fi, Tailscale SSH)
froze or lagged. Neither side's logs showed a cause (see issue #81 for the investigation), so this logs the server and the
path continuously, to line a slow moment up against.

## What it records (`scripts/sshmon.py`, cron every minute on production, 6 samples 10 s apart)
- Tailscale ping and plain LAN ping to the gaming PC: ms, and whether the Tailscale path is `direct` or a DERP relay.
- `ts_endpoint` (added 2026-10-18): the address family the Tailscale ping used: `v6`, `lan4`, `wan4`, or the DERP relay name. Tailscale switches
  between these on its own; `--report` prints the counts and how many times it switched, to test whether freezes line up with a switch.
- LAN OpenSSH sessions (`:22`): max TCP rtt, unacked, retransmits since the previous sample, queued bytes.
- Count of Tailscale SSH sessions (`tailscaled be-child ssh`). These never appear in `ss` on :22.
- PSI avg10 for io / memory / cpu, load, swap in/out per second.
- Three "does this host feel slow" probes: 0.5 s sleep overshoot (scheduler delay), fork+exec, 4 KB write+fsync on /home.

Server side only: it cannot see a stall inside the Windows terminal.

## Using it
```
scripts/sshmon.py --report --hours 24        # percentiles + every slow sample
scripts/sshmon.py --report --around 21:40    # +/-10 min around a time you felt a freeze (today)
scripts/sshmon.py --once                     # one sample, for testing
```
A sample is "slow" if the Tailscale path is not direct, or any column crosses its line (`THRESH` in the script).
Reading it: path fine + probes fine + a freeze = the problem is on the client (terminal/Wi-Fi). Path not direct or
ping spikes = path. Probe spikes or io_full high = this host.

## lossmon (added 2026-10-18)
`scripts/lossmon.py`, cron every minute: ~5 pings/s for 58 s to the gaming PC (Wi-Fi) and to r9 (wired control), one row
per target per minute in `~/logs/sshmon/lossmon.csv` (sent, lost, longest run of missed pings, max rtt) and every run of
>=3 missed pings in `bursts.csv`. sshmon's single ping per 10 s missed the bursts entirely (a 30 s test showed 0 %
minutes before a 31-47 % one). `scripts/lossmon.py --report --hours 24` or `--around HH:MM`. The control matters: on
2026-10-18 r9 lost packets at the same moments as the PC, so loss to the PC alone does not mean Wi-Fi.

## Files / cost
Log `~/logs/sshmon/sshmon.csv` (outside the repo: holds LAN/tailnet addresses), ~1.3 MB/day, rotated to `.old` at 25 MB
(about 3 weeks). A run is ~1 minute of sleeping with ~1% CPU in bursts. No sudo, no packages, stdlib only.

## Remove
Delete the cron line (`crontab -e`) and `rm -rf ~/logs/sshmon`.

## Baseline (2026-10-18, idle): ping direct 3-4 ms, LAN ping 4-6 ms, fsync ~2 ms, io full ~0.7 %.
Existing LAN SSH sessions show a TCP rtt of ~45 ms against a 4 ms ping: probably Windows delayed-ACK on small
interactive packets rather than the path. Watch whether it rises.
