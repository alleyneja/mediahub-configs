# Central logs (Loki + Grafana + Alloy)

Issue: mediahub-issues #71 (Seerr→Plex failed silently for 4+ weeks; containers "up" but broken).

| Piece | Where | Notes |
|---|---|---|
| Loki 3.7.8 | r9, `stacks/loki-r9`, `100.121.244.45:3100` | tailnet-only (no auth); 90-day retention (`loki-config.yml`); data `/srv/docker/loki` |
| Grafana 13.2.3 | r9, **https://grafana.lan** (caddy-r9 + AdGuard rewrite → r9); fallback `100.121.244.45:3000` | login `admin`; password in `stacks/loki-r9/.env` on r9 (gitignored); Loki datasource provisioned |
| Alloy v1.20.1 collector | r9 (`stacks/loki-r9`) and i7 (`stacks/alloy`) | read-only Docker-socket log reader; labels `container`, `host` (`i7`, `r9`) |

~16 MB/day of raw logs fleet-wide (measured 2026-10-07: i7 12 MB, r9 4 MB). Alloy backfilled the history Docker still held (up to 90 days).
Example query: `{container="seer"} |= "Scan interrupted"` (Explore → Loki).

Deploy: i7 `cd stacks/alloy && docker compose up -d`; r9 `cd stacks/loki-r9 && docker compose up -d`.
Not done yet: error-pattern digest, per-service "hasn't done its job" rules, x51 collector (powered off).
Note: r9's repo clone has diverged from origin (history rewrite, issue #64; 3 commits exist only on r9), so these two stack
folders were copied to r9 by `tar` rather than `git pull`. Re-sync them when the clone is reset.

## Front door and monitoring (added 2026-10-07)
- **Caddy:** `grafana.lan` block in `stacks/caddy-r9/Caddyfile` (live copy `/srv/docker/caddy-r9/Caddyfile` on r9, root-owned, `sudo cp`; admin API is off so changes need `docker restart caddy-r9`, which blips Stirling PDF for a few seconds). Grafana joins `r9_internal`. Loki is deliberately **not** proxied (unauthenticated collector API, tailnet IP only).
- **AdGuard:** rewrite `grafana.lan → 100.121.244.45` (added through the API; mirror `adguard/AdGuardHome.yaml` hand-edited; x51 replica syncs hourly).
- **Homepage:** "Grafana Logs" tile in Infrastructure.
- **Uptime Kuma** (monitors 45 and 46, linked to Discord): "Grafana (logs)" = `https://grafana.lan/api/health` (real client path), "Loki (logs)" = `http://100.121.244.45:3100/ready`. Created with Kuma stopped, DB owned by root (use `sudo sqlite3`). **Gap:** these prove the services are up, not that logs are arriving; a freshness check (each host sent a line in the last 10 min) is still to do.
