# Central logs (Loki + Grafana + Alloy)

Issue: mediahub-issues #71 (Seerr→Plex failed silently for 4+ weeks; containers "up" but broken).

| Piece | Where | Notes |
|---|---|---|
| Loki 3.7.8 | r9, `stacks/loki-r9`, `100.121.244.45:3100` | tailnet-only (no auth); 90-day retention (`loki-config.yml`); data `/srv/docker/loki` |
| Grafana 13.2.3 | r9, `100.121.244.45:3000` | login `admin`; password in `stacks/loki-r9/.env` on r9 (gitignored); Loki datasource provisioned |
| Alloy v1.20.1 collector | r9 (`stacks/loki-r9`) and i7 (`stacks/alloy`) | read-only Docker-socket log reader; labels `container`, `host` (`i7`, `r9`) |

~16 MB/day of raw logs fleet-wide (measured 2026-10-07: i7 12 MB, r9 4 MB). Alloy backfilled the history Docker still held (up to 90 days).
Example query: `{container="seer"} |= "Scan interrupted"` (Explore → Loki).

Deploy: i7 `cd stacks/alloy && docker compose up -d`; r9 `cd stacks/loki-r9 && docker compose up -d`.
Not done yet: error-pattern digest, per-service "hasn't done its job" rules, x51 collector (powered off).
Note: r9's repo clone has diverged from origin (history rewrite, issue #64; 3 commits exist only on r9), so these two stack
folders were copied to r9 by `tar` rather than `git pull`. Re-sync them when the clone is reset.
