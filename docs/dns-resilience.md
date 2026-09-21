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
| Home Wi-Fi (router DHCP) | AdGuard, `1.1.1.1` | unchanged, already had a backup |

Primary path is unchanged: AdGuard -> Quad9 over HTTPS, so ISP lookups stay hidden while
everything is healthy. Fallback traffic is cleartext by design (D-R2).

## Verified

- Tailscale lists both resolvers; `lan` split route still points at AdGuard.
- After adding the second server, Gaming-PC's lookups still reach AdGuard and blocking
  still works (187 lookups / 34 blocked in 6 min).
- Kuma resolves `discord.com` with both servers configured.

## NOT verified

- **Actual failover.** Nobody stopped AdGuard to confirm Kuma, tailnet devices, or
  AdGuard's own fallback take over. Needs a deliberate short AdGuard outage.
- Root cause of the DoH stalls.
- Whether Tailscale ever sends queries to the secondary while AdGuard is healthy (would
  bypass ad-blocking). One 6-minute sample showed no bypass.

## Open

- Off-house alerting: Kuma runs on the box it monitors, so a whole-server outage cannot
  alert anyone. Candidate: mediahub-r9 or an external heartbeat. Not decided.
- Tailscale `down` on the server has no guard. The alert in D-R5 should cover it.
