Mirror of production's Netdata health overrides, live at `/srv/docker/visibility/netdata/config/health.d/` (the
config dir itself is host-only because `health_alarm_notify.conf` holds the Discord webhook). Same-named files there
replace Netdata's stock `health.d/*.conf`. Reload with `docker exec netdata netdatacli reload-health`.

2026-09-25 routing change (the only edits vs stock; thresholds untouched):
- `used_swap` -> silent. Swap sits near-full with idle pages and no swap activity, which is harmless.
- `30min_ram_swapped_out` -> sysadmin (Discord). The machine is actively swapping, which slows things down.
- `ram_available` -> sysadmin. Real RAM is running low.
- `oom_kill` -> sysadmin. The kernel killed a process for lack of memory.
