# DNS Resilience Decision

**Status:** decided 2026-09-20
**Relates to:** `network-architecture.md` requirement **R3** (household internet must survive
mediahub-production being down), `discord-alerts.md`

## What happened

On 2026-09-20 the household and Plex users lost internet for roughly 3 hours. Two things
overlapped:

1. AdGuard's only upstream was Quad9 over HTTPS, and its "fallback" was also DoH
   (Cloudflare, Google). Those HTTPS lookups stalled, so AdGuard had no working path.
   The cause of the stall is **unconfirmed** (gateway SBG8300 or ISP path suspected).
2. Tailscale on mediahub-production was stopped (`tailscale down`, logged 18:14:49). Every
   tailnet device used `100.104.43.6` as its only DNS server, so they lost all DNS.

No Discord alert went out: Uptime Kuma's `dns:` override listed only AdGuard, so it could
not resolve `discord.com` while AdGuard was failing.

This was an R3 violation. Nothing had been written down about what DNS should do when its
upstream failed, so each layer had a single point of failure.

## Requirements (Jay, 2026-09-20)

| # | Requirement |
|---|---|
| D-R1 | `.lan` names MAY fail when the server is down, provided normal internet works |
| D-R2 | Uptime beats privacy. Cleartext DNS fallback is acceptable during an outage |
| D-R3 | Tailnet devices MUST have a backup resolver. This cannot be a regular occurrence |
| D-R4 | Hide lookups from the ISP where possible, never at the expense of uptime |
| D-R5 | An outage MUST reach Discord even while AdGuard is failing |

## Decision and changes

| Layer | Before | After |
|---|---|---|
| AdGuard `fallback_dns` | DoH: Cloudflare, Google | plain UDP: `9.9.9.10`, `1.1.1.1` (commit 4216f5a) |
| Uptime Kuma `dns:` | AdGuard only | AdGuard, `1.1.1.1` (commit 71faba8) |
| Tailscale global nameservers | `100.104.43.6` only | `100.104.43.6`, then Cloudflare `1.1.1.1`, `1.0.0.1` and two IPv6 addresses (admin console, not in repo) |
| AdGuard `upstream_timeout` | 10s (stalled lookup answered after 30s = 3 tries) | 3s (stalled lookup answered after ~9s) |
| Home Wi-Fi (router DHCP) | AdGuard, `1.1.1.1` | unchanged, already had a backup |

Primary path is unchanged: AdGuard -> Quad9 over HTTPS, so ISP lookups stay hidden while
everything is healthy. Fallback traffic is cleartext by design (D-R2).

## Verified

- Tailscale lists both resolvers; `lan` split route still points at AdGuard.
- After adding the second server, Gaming-PC's lookups still reach AdGuard and blocking
  still works (187 lookups / 34 blocked in 6 min).
- **Clean stop (AdGuard container stopped, 42s):** server, Kuma and direct lookups all kept
  resolving with no gaps. Failure was instant (connection refused), so this is the easy case.
- **Stall (AdGuard UP, TCP/443 to Quad9 DoH dropped, i.e. the Sep 20 failure mode):**
  - Kuma and the server's own resolver kept working via their second resolver.
  - A client using AdGuard alone got NO answer within 15s.
  - AdGuard's plain-DNS fallback DOES work but only after 3 x `upstream_timeout`:
    30s at 10s (query log: `upstream=1.1.1.1:53`, elapsed 30025ms), ~9s at 3s.
  - Conclusion: AdGuard's fallback alone is too slow for clients. The real safety net is a
    second resolver on each client (Kuma, server, tailnet, router DHCP).

## NOT verified

- Real client behaviour during a stall (how long Windows/iOS/Android wait before switching
  to their secondary). Gaming-PC's experience during the test was not reported.
- Whether `upstream_timeout` should go lower (1s would give ~3s stalls; trade-off is
  occasional false timeouts on slow lookups falling back to cleartext).
- Root cause of the original DoH stalls (SBG8300 or ISP path suspected).
- Whether Tailscale ever sends queries to the secondary while AdGuard is healthy (would
  bypass ad-blocking). One 6-minute sample showed no bypass.
- Tailscale-off-on-server failure mode (tailnet resolver unreachable) was not simulated.

## Open

- Off-house alerting: Kuma runs on the box it monitors, so a whole-server outage cannot
  alert anyone. Candidate: mediahub-r9 or an external heartbeat. Not decided.
- Tailscale `down` on the server has no guard. The alert in D-R5 should cover it.

## x51 replica AdGuard (2026-09-24)

A replica AdGuard runs on x51 (`stacks/adguard-x51/`), synced hourly from production by
`scripts/adguard-sync-x51.py` (cron `17 * * * *`, log `~/logs/adguard-sync-x51.log`). Its upstreams are
plain UDP (`9.9.9.10`, `1.1.1.1`) with Quad9 DoH as fallback, deliberately different from production's
DoH so one transport stall does not take both down. `1.1.1.1` stays as the last client-side resolver
everywhere; x51 is added, never substituted (a clean-stop test cannot cover the Sep 20 stall failure).

**Failover test, 2026-09-24 (r9 as client, runtime DNS list `.21 .20 1.1.1.1`):**
- Production AdGuard stopped 24 s. r9 switched to `.20` on its first query, `.lan` and public lookups
  answered in ~0.1 s with no gaps; x51's query counter rose 42 -> 60.
- Production restarted: it took longer than 3 s to answer again (refused at +3 s; a 2.2 M-rule blocklist loads).
  r9 returned to `.21` within ~2 min via the resolved-prefer-adguard timer (its resolved restart caused
  one refused query, ~1 s).
- Not tested: a stall (AdGuard up, upstream path dropped) with x51 in the chain; real phone/PC clients.
