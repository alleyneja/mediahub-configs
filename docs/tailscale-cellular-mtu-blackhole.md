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
