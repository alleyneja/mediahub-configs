# Large transfers stall over cellular: Tailscale MTU vs carrier path MTU

**Date:** 2026-08-18
**Symptom:** Stirling PDF showed a white screen on iPhone Safari over cellular,
but loaded instantly on WiFi. Same server, same Tailscale IP, same certificate.

## What was actually happening

Not an access problem — the phone reached the server fine. Caddy's access log
(enabled temporarily on the `stirling.lan` vhost) showed the page shell arriving
and the app bundle **truncating**:

```
11:46:59  /                          200        689   <- shell, fine
11:47:00  /manifest.json             200        520   <- fine
11:47:21  /assets/index-*.js         200    343,680   <- died at 34% of 1,013,785
11:54:27  /assets/index-*.js         200  1,013,785   <- WiFi, complete, 0.041s
```

Stirling is a single-page app, so no JS bundle = valid HTML rendering nothing.

## Root cause

The cellular path could not carry Tailscale's packet size. Measured against the
phone while it sat on a healthy **direct** cellular connection:

```
inner IP 1232B: OK
inner IP 1248B: OK
inner IP 1256B: OK
inner IP 1264B: FAIL   <- 100% packet loss
```

Tailscale's default tunnel MTU is **1280**. The carrier path topped out at
**1256** — a 24-byte shortfall. Oversized packets were dropped silently, with no
ICMP "fragmentation needed" coming back, so the sender never learned. A
classic PMTU blackhole.

Two factors made it fatal rather than merely slow:

1. Small responses fit under the wall and always succeeded, so the service
   looked "up" — only large transfers ramped to full-size packets and died.
2. `net.ipv4.tcp_mtu_probing` was **0**, so the kernel never probed downward.
   The connection stalled permanently instead of recovering at a smaller size.

## The trap: caching hides it

After one successful WiFi load the problem *appears* to fix itself. Stirling
serves assets with `cache-control: max-age=31536000, immutable`, so the heavy
files are never re-requested. Subsequent cellular loads only fetch small JSON
and lazy chunks, all under the wall, and everything looks fine — while the
network fault is completely unchanged.

It returns on: a version bump (new bundle hash = cache miss), cache eviction, or
any device loading the app fresh over cellular.

**This is not app-specific.** Sonarr's JS bundles hit the same wall on the same
phone (`writing: client disconnected`). Plex and Audiobookshelf were unaffected
because ranged media requests are chunked small.

## Fix

`/etc/default/tailscaled` (mirrored at `system/default-tailscaled`):

```
TS_DEBUG_MTU=1200
```

`/etc/sysctl.d/99-tailscale-mtu.conf` (mirrored at `system/99-tailscale-mtu.conf`):

```
net.ipv4.tcp_mtu_probing = 1
```

Then `systemctl restart tailscaled`. Confirm with `ip link show tailscale0`,
which must report `mtu 1200`.

1200 sits under both the 1256 wall measured on the direct path and the worse
DERP-relay path measured earlier the same day (which also failed at 1268B and
showed 13% loss even at 928B). Cost is roughly 6% more packets per MB — not
measurable in practice.

`tcp_mtu_probing = 1` is a backstop, not the fix: if a future path is tighter
than 1200, connections degrade and recover instead of stalling dead.

## Diagnosing a recurrence

```bash
# Ladder the path while the affected device is ON the suspect network.
for s in 900 1100 1172 1200 1240 1252; do
  ping -c 3 -W 4 -M do -s $s <peer-tailnet-ip> >/dev/null 2>&1 \
    && echo "$s OK" || echo "$s FAIL"
done
```

Check the peer's endpoint first — `tailscale status` — or you will measure the
wrong path. `direct 192.168.x.x` means the device is on the LAN and the result
says nothing about cellular.

Caddy logs only errors by default. To see truncation you need an access log on
the vhost; `size` in the access log versus `content-length` is the tell.

---

# ⚠️ REVERTED 2026-08-20 — the 1200 fix broke everything else off-wifi

**Do not lower Tailscale's MTU below 1280 again.**

**Symptom it caused:** Jay and Maria both got long waits and frequent timeouts on
Homepage, Lidarr, Plex and Seerr — but **only off wifi**. At home everything was
fine, which is what made it look unrelated to this change.

**Mechanism.** IPv6 has a hard minimum link MTU of **1280**. With
`TS_DEBUG_MTU=1200` the kernel refuses to configure IPv6 on `tailscale0` at all:

```
ip -6 addr show tailscale0     ->  (nothing)
ping6 fd7a:115c:a1e0::c832:2b06 ->  Network is unreachable
```

Meanwhile Tailscale still advertised the node's IPv6 ULA and MagicDNS still served
the AAAA record. **iOS prefers IPv6.** So every new connection tried an address
that answered nothing, waited out the timeout, and only then fell back to IPv4 —
on every service, on every connection. WiFi was unaffected because `*.lan`
resolves to LAN IPv4 through AdGuard and never touches the tailnet.

**The revert:** comment out `TS_DEBUG_MTU` in `/etc/default/tailscaled`,
`systemctl restart tailscaled`. Verified after: interface MTU 1280, v6 address
present, `ping6` 0% loss, and both `homepage.lan` and `romm.lan` returning 200
over **both** the v4 and v6 tailnet paths in under 20 ms.

**What still protects the original blackhole:** `net.ipv4.tcp_mtu_probing = 1`
(`/etc/sysctl.d/99-tailscale-mtu.conf`) is **kept** — that is the supported remedy,
and it makes TCP shrink its own segments when it detects the wall rather than
stalling forever.

**If the cellular stall returns:** cap IPv4 tailnet traffic with a route-level MTU
(`ip route ... mtu 1256` on the 100.64.0.0/10 route) rather than the interface MTU.
That leaves the link legal for IPv6. Also re-measure first — the 1256 figure was one
carrier path on one day, and paths change.

**Wider lesson:** a change scoped to one symptom on one device silently degraded
every service for every user, in a place nobody was looking. The measurement that
justified it was correct; the blast radius was never checked.

---

## 2026-08-21 — the stall returned, and the actual fix

Reported symptom: Aurral taking ~30s to load or crashing on an iPhone on cellular,
while feeling normal on wifi on both phone and desktop. Lidarr and Seerr felt slow
off-wifi too.

**Re-measured, per the instruction above.** With the phone live on T-Mobile
(107 ms, CGNAT `172.58.135.182`), DF-set pings over `tailscale0`:

| inner IP packet | result |
|---|---|
| 1028 B | 0% loss |
| 1260 B | 0% loss |
| 1262 / 1264 / 1266 B | 100% loss |
| 1268 / 1276 / 1280 B | 100% loss |
| 1288 B and up | local `EMSGSIZE` (exceeds the 1280 interface MTU) |

So the real wall is **1260**, not the 1256 recorded in August. The 1288+ rows
matter: those fail *locally* with counted errors, which is how we know 1262–1280
are genuine silent drops on the path rather than the kernel refusing them here.

`tailscale0` is at MTU 1280 and cannot go lower (see above). Every full-size
packet was being dropped.

**Why it looked like an application bug:** small responses fit under the wall and
worked fine, so the app authenticated and rendered its shell. Only the large
transfers died — Aurral's 280 KB JS bundle and its ~95 KB artwork. `tcp_mtu_probing=1`
does eventually recover, but only after several retransmit timeouts, and
`ip tcp_metrics` held no cached MSS for any peer, so each new connection re-paid
that cost. That is the 30 seconds.

**Why wifi was fine:** `aurral.lan` resolves to `100.104.43.6` — an **A record only,
no AAAA** — so this is IPv4 over the tunnel in both cases. On wifi Tailscale uses a
direct LAN path (`192.168.0.x`, MTU 1500) and 1280 fits easily. On cellular it does not.

### The fix: TCP MSS clamping, not interface MTU

Route-level MTU was the plan recorded above. It was rejected on inspection:
tailscaled installs **per-peer `/32` routes in table 52** and rewrites them as peers
come and go, so any `mtu` set there gets clobbered.

Instead, clamp TCP MSS on `tailscale0` — `/etc/nftables-ts-mss.conf`, applied at boot
by `ts-mss-clamp.service`. MSS is a TCP-level constraint, so it is **legal below the
IPv6 1280 floor** and never touches addressing. It structurally cannot repeat the
August outage.

    inner budget 1240 (20 B margin under the measured 1260 wall)
    IPv4 MSS = 1240 - 20 - 20 = 1200
    IPv6 MSS = 1240 - 40 - 20 = 1180

Both `prerouting` (rewrites what a peer advertises to us, capping what *we* send)
and `postrouting` (capping what *they* send) are required. Interfaces are matched by
**name**, so the rules survive tailscaled recreating `tailscale0`.

`nftables.service` is deliberately left **disabled** — its `/etc/nftables.conf` opens
with `flush ruleset`, which would wipe Docker's and Tailscale's rules. The dedicated
`table inet ts-mss` coexists with the iptables-nft tables without touching them.

**Verified:** a new tailnet connection negotiates `mss:1188` (+12 B timestamps
+20 TCP +20 IP = 1240 B on the wire); a LAN/docker connection is untouched at
`mss:1448 pmtu:1500`. `tailscale0` still holds both IPv6 addresses at MTU 1280.

**Revert:** `sudo systemctl disable --now ts-mss-clamp.service`

### Also fixed: compression was off everywhere Caddy served it

Caddy does not compress by default and no site block asked it to. Aurral's upstream
ships plain text, so a cold load was **425 KB** of uncompressed JS/CSS — on top of a
broken MTU. Added a `(compress)` snippet, imported only where the upstream does not
already compress:

| site | before | after |
|---|---|---|
| audiobookshelf | 2,549,852 B | 688,800 B (72% smaller) |
| calibre-web | 842,909 B | 507,515 B (39%) |
| aurral | 425,281 B | 131,407 B (69%) |
| homepage | 310,213 B | 72,656 B (76%) |
| scanopy | 68,200 B | 28,902 B (57%) |

Measured as already self-compressing — **do not add** the snippet to these: seer,
lidarr, radarr, prowlarr, immich, mealie, romm, uptimekuma, pterodactyl. Notably
**Seerr already compressed**, so its slowness was the MTU wall, not payload size.

**Lesson, extending the one above:** the August measurement was right and the
prescribed remedy was still wrong, because it was written without checking that
tailscaled owns those routes. Re-measure *and* re-check the mechanism.

---

## 2026-08-22 — it was never the MTU, and never the ISP

The Aurral investigation continued and overturned most of the section above.
Recording what the evidence actually showed, including the wrong turns, because
three of them were caused by concluding from a single signal.

### Wrong turns (each from one unconfirmed reading)

1. **"Comcast is dropping 8% of packets."** Based on ICMP loss to public DNS
   resolvers. ISPs deprioritise ping. A TCP test showed **458 Mbps down / 36 Mbps
   up at 0.07-0.23% retransmits** — the line is healthy. Nearly sent Jay to
   support with a fabricated problem.
2. **"Aurral's 14 MB of artwork is the cause."** Killed by Sonarr failing at
   **0.13 MB**, smaller than pages that worked fine.
3. **"The LAN path measures 0 Mbps."** The test page's relative URLs resolved to
   a path with no handler; Caddy returned **200 OK with an empty body**, so the
   page computed 0 Mbps from a "successful" empty response. Validate the
   instrument before trusting the reading.

### What the evidence actually supports

| Path | Result |
|---|---|
| Phone → `192.168.0.21` direct, no tunnel, wifi | **95 / 421 / 477 Mbps** |
| Phone → tunnel, home wifi | **106 / 355 Mbps** (39 MB in ~6 s on TS counters) |
| Phone → tunnel, **cellular** | **14-22 KB/s**, 1 MB files truncating at 232-798 KB after 11-56 s |
| Ping through tunnel → phone on cellular | **7.5% / 10% / 16% loss**, RTT 137 ms, jitter 47 ms |
| Ping through tunnel → Gaming-PC on LAN (**control**) | **0% loss**, RTT 4.5 ms |

The control is what makes this conclusive: same tunnel, same interface, same
server, zero loss to a LAN peer. **The loss is on the cellular radio leg.**

Ruled out with evidence, not assumption:
- **Not MTU.** The MSS clamp is firing (172 in / 455 out), the wall re-measured
  at exactly 1260 (reproducible), and our packets are 1240 — comfortably under.
- **Not DERP.** Direct UDP path confirmed, `curaddr=172.58.135.4:62205`.
- **Not the server.** 0% loss to a LAN peer; files serve locally at 6,480+ Mbps.
- **Not the home internet.** See the TCP numbers above.

Arithmetic closes: Mathis gives MSS/(RTT·√p) = 1200/(0.137·0.316) ≈ **27 KB/s**
for 10% loss at 137 ms. Caddy measured **14-22 KB/s**. Same ballpark.

### The fix: BBR congestion control

`system/sysctl-99-tcp-bbr.conf`. CUBIC (the default) treats every lost packet as
congestion and backs off. On a radio link the loss is interference and handoffs,
not congestion, so CUBIC throttles itself for the whole transfer — exactly the
collapse measured above. BBR models bandwidth and RTT instead of reading loss as
a stop sign, and holds throughput on lossy links. At least as good as CUBIC on
clean LAN paths.

Verified three ways: `sysctl` reports bbr; live sockets negotiate it (new
connections show bbr while pre-existing ones keep cubic until they close);
`sysctl --system` reapplies at boot. Revert by deleting the file and
`sysctl -w net.ipv4.tcp_congestion_control=cubic`.

### Considered and deprioritised

Pointing the 33 AdGuard `.lan` rewrites at `192.168.0.21` instead of the tailnet
IP would stop LAN devices tunnelling to a server ten feet away (477 vs ~1 Mbps at
the time). All three prerequisites verify — `ip_forward=1`, subnet route
`192.168.0.0/24` advertised **and** approved, and cellular requests to
`192.168.0.21` confirmed arriving in Caddy's access log. **Not done:** Jay's pain
is cellular and away-from-home, and this only helps home wifi, which already
feels fine. Ad blocking would have been unaffected either way — `filters:` (line
107) and `rewrites:` (line 267) are independent sections.

### Diagnostic tooling added

- `scripts/netcheck.sh` — repeatable baseline (loss, RTT, TCP throughput with
  retransmit counting, latency under load). Run before/after a change and diff.
- `scripts/ts-watch.sh` — samples the tailnet path to a peer during a transfer;
  distinguishes DERP relay from direct, and proves bytes actually moved.
- Temporary: `homepage.lan/speedtest` and an `http://192.168.0.21` block in the
  Caddyfile, plus `/srv/docker/caddy/config/speedtest/`. **Remove when closed.**
