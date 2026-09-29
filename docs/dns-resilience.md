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
| Tailscale global nameservers | `100.104.43.6` only | `100.104.43.6`, x51 `100.124.234.117`, then AdGuard public DNS `94.140.14.14`, `94.140.15.15` and IPv6 `2a10:50c0::ad1:ff`, `::ad2:ff` (admin console, not in repo). **Originally Cloudflare `1.1.1.1`/`1.0.0.1` + IPv6; replaced 2026-09-28, see "Ad leak" below** |
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
- ~~Whether Tailscale ever sends queries to the secondary while AdGuard is healthy.~~
  **Answered 2026-09-28: YES, under concurrent load. See "Ad leak" below.** The one 6-minute
  sample above was too short and too sequential to catch it.
- Tailscale-off-on-server failure mode (tailnet resolver unreachable) was not simulated.

## Open

- Off-house alerting: Kuma runs on the box it monitors, so a whole-server outage cannot
  alert anyone. Candidate: mediahub-r9 or an external heartbeat. Not decided.
- Tailscale `down` on the server has no guard. The alert in D-R5 should cover it.

## Ad leak through the Tailscale fallback (2026-09-28)

**Symptom:** Jay's phone and PC (both on Tailscale) showed ads that used to be blocked.

**Not the cause:** the x51 replica. Its blocklists are synced hourly and it blocks the same
domains as production (checked with `dig`). Both AdGuards return `0.0.0.0` for the ad domains.

**Cause:** Tailscale's forwarder does not try resolvers in order. It races them, and the first
answer wins. Cloudflare `1.1.1.1` (auto-upgraded by Tailscale to DoH, `cloudflare-dns.com`)
does not block ads and often answers as fast as AdGuard (~28 ms vs 27-57 ms, because AdGuard
adds a DoH hop to Quad9). So every lookup that Cloudflare won came back unblocked. Cloudflare
was added to the Tailscale list on 2026-09-20 to satisfy D-R3, and D-R3 has no ad-blocking
clause, so nothing flagged it.

**How it was proven (temporary ephemeral Tailscale node in userspace mode, own socket/state,
`tailscale dns query`, the only way to get the tailnet forwarder without touching a host's DNS;
`tailscale dns query` on production and r9 gives SERVFAIL for everything because both have
Tailscale DNS disabled, so those are not valid test points):
- 200 sequential lookups of unique ad subdomains: 200 blocked. 60 sequential `ads.youtube.com`: 60 blocked.
- Concurrent (30 in flight): 14 of 300 unique `googlesyndication.com` names returned NXDOMAIN (AdGuard
  never does that for a blocked name); `ads.youtube.com` and `googleads.g.doubleclick.net` bursts
  returned real Google IPs on ~1-3% of responses. Only Cloudflare could have produced those.
- A phone loading a page fires many lookups at once, which is the concurrent case.

**Fix (Jay, admin console, 2026-09-28):** replaced the four Cloudflare entries with AdGuard public
DNS (`94.140.14.14`, `94.140.15.15`, `2a10:50c0::ad1:ff`, `2a10:50c0::ad2:ff`). It keeps a
fallback for a double AdGuard outage (D-R3, D-R2) and blocks ads if it wins a race.
**Retest:** same concurrent test, 1,500 lookups (`ads.youtube.com` x900,
`googleads.g.doubleclick.net` x300, unique `googlesyndication.com` x300): all `0.0.0.0`, 0 leaks.

**Why not simply remove the fallback:** no tailnet DNS at all during a double AdGuard outage,
the Sep 20 failure D-R3 exists to prevent. Tailscale has no "ordered, fallback only on failure"
setting (as far as we know), so the list contents are the only control.

**Trade-offs still open:**
- AdGuard public DNS runs one list (AdGuard DNS filter); production runs 13. A raced lookup can
  still leak niche trackers that only production blocks (e.g. `telemetry.microsoft.com`,
  `ads.tiktok.com` were unblocked by public DNS in a one-off `dig`; not tested under load).
- Public AdGuard DNS is plain UDP to a third party here, same exposure class as `1.1.1.1` was (D-R2).
- Ads that never touch DNS blocking (first-party YouTube/in-app ads, browser DoH, iCloud Private
  Relay) are outside this fix.
- The 1.1.1.1 entries in router DHCP and AdGuard's own upstream/fallback are unchanged on purpose:
  they are not in the client race path that leaked.

**Standing rule:** any resolver in a client list that can win a race must block ads too. Do not
add a non-blocking public resolver to the Tailscale list. (Supersedes the 2026-09-24 note
"never remove 1.1.1.1 from client lists" for the Tailscale global list only.)

Tracker: alleyneja/mediahub-issues#60 (private).

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

**Real-client test, 2026-09-24 (jays-iphone over Tailscale, production AdGuard stopped 60 s):**
- All 29 of the iPhone's lookups in the window were answered by x51: `.lan` rewrites (homepage, lidarr,
  sabnzbd, calibre-web, auth, bookshelf-ebooks) in <0.1 ms, public names via plain UDP (`1.1.1.1:53`,
  `9.9.9.10:53`, 27-140 ms), and ad-blocking still worked (`sentry.servarr.com` filtered by blocklist).
- Also observed: even with production healthy, Tailscale clients send some lookups to x51 (the iPhone had
  15 before the test), so drift between the two AdGuards is user-visible, not just a failover concern.
  The hourly sync is what keeps that harmless.

