# Network Architecture

**Status:** approved 2026-08-22
**Purpose:** record *why* the network is shaped the way it is, so future changes are
corrections to a single document rather than fresh guesses.

This document exists because of how 2026-08-22 went. A full day was spent chasing
symptoms — MTU, BBR, Tailscale ports, packet loss, port forwarding — and the day ended
with a recommendation to buy a router that the evidence did not support. The failure was
not any individual measurement. It was that no one had written down what the network is
supposed to *do*, so there was nothing to test a diagnosis against.

Requirements first, then network needs, then design, then hardware. In that order.

---

## 1. Requirements

| # | Requirement | Origin |
|---|---|---|
| R1 | Exactly one internet-facing service: **Pterodactyl** (strangers connect inbound) | stated |
| R2 | **Amended 2026-08-22.** RomM / arcade downloads and remote admin are tailnet-only. **Plex is not** — it is shared with people outside the tailnet (e.g. Ed), so it must work without Tailscale. Relay is an accepted fallback. | stated |
| R3 | Household internet must survive mediahub-production being down | stated |
| R4 | Trust set is closed and known: Jay, Mafe, brother, household devices. No public wifi guests, no strangers on the LAN | stated |
| R5 | Local Moonlight: ceiling-mounted Dangbei projector, wifi, ~40 ft, one interior wall | stated |
| R6 | Storage traffic and client traffic share one 1 Gb NIC — but see §5, the penalty is far smaller than first assumed | measured |
| R7 | AdGuard is functional but is a single point of failure for all LAN DNS | measured |

### Non-requirements

Recorded deliberately, so they are not re-litigated:

- **Guest network isolation.** R4 says the trust set is closed. Revisit if that changes.
- **IoT segmentation.** No untrusted IoT of consequence today.
- ~~**Public Plex access.**~~ Withdrawn 2026-08-22 — see R2 as amended. Plex is shared beyond the tailnet.

---

## 2. Trust model

Two zones, not five:

- **Trusted** — everything on the LAN and everything on the tailnet. Same practical
  privilege level. This is a deliberate simplification justified by R4.
- **Hostile** — the open internet. Reaches exactly one service (R1).

Tailscale, not per-service authentication, is the primary gate. This is consistent with
the existing decision to leave Vaultwarden self-signups enabled: the tailnet is the
perimeter.

---

## 3. Three-tier assessment

The core / distribution / access model is a *campus* model. It assumes multiple
distribution blocks needing a backbone between them. This is one building with one wiring
location, so core and distribution legitimately collapse. What transfers is the separation
of **concerns**, not the count of boxes.

### As-is (2026-08-22)

```
CORE          does not exist as a separate path.
              Server<->NAS NFS shares enp3s0 with client traffic, but the
              link is full duplex, so the two directions do not contend.

DISTRIBUTION  ARRIS SBG8300 — routing, NAT, DHCP, firewall.
              0 VLANs. One flat 192.168.0.0/24.

ACCESS        ARRIS SBG8300 again — internal switch + AC2350 radio.
              Projector, phones, consoles, server, NAS: one segment.
```

All three tiers are one box. Collapsed tiers are only a defect where a requirement pushes
back on them. Checking each:

| Tier | Requirement pressure | Verdict |
|---|---|---|
| Distribution | R1 needs one working inbound forward. Verified working — 441 distinct public source IPs were observed arriving on `enp3s0`. R4 removes the VLAN pressure. | **Adequate** |
| Access | Measured 2026-08-22: -52 dBm, 702 Mbps PHY, 5 GHz, 0% loss, 1.9 ms jitter. ~4-5x Moonlight's need. | **Adequate** |
| Core | Measured: shared 1 GbE read ceiling, ~948 Mbps. Not reached under real load. | **Not currently a constraint** |

---

## 4. Target architecture

### 4.1 Edge

One forward, for Pterodactyl. Nothing else.

**Status 2026-08-22: R1 is NOT currently met.** An external node probed TCP/25500 while
`tcpdump` watched `enp3s0` from inside the boundary — **zero packets arrived**. Unlike the
Plex case this is a true negative, because the instrument was inside. The gateway is not
forwarding the Pterodactyl ports (25500-25502). Recent container logs show no external
players either, so this looks like a forward that was never configured rather than
something that broke. If strangers are meant to connect, these ports need forwarding.

**Docker publishes container ports around UFW.** `DOCKER-USER` is empty, so Docker's DNAT
rules are not subject to UFW's `deny (incoming)` policy. 32 container ports are bound to
`0.0.0.0` — *arr stack, RomM, Authentik, Calibre, Homepage and others. What actually keeps
them off the internet is the gateway's NAT, not the firewall. Consistent with R4 (LAN is
trusted) but worth knowing precisely:

- UFW's rule list overstates the protection in place. Host-networked services (Plex,
  AdGuard) obey it; container-published ports do not.
- Anything that can create a port forward — notably **UPnP**, which had stale leases on
  this gateway — can expose a container port without touching UFW. UPnP should be off.

The TCP/32400 forward is **removed**. It contradicts R2, and it was drawing scans from
441 distinct public addresses to no benefit. UFW's default-deny was already enforcing the
correct policy; the gateway forward was the misconfiguration.

### 4.2 Plex path

**Revised 2026-08-22.** Remote access stays **on**, so relay is always available and Plex
works for people who will never be on the tailnet. `customConnections` publishes the
tailnet address, so anyone who *is* on Tailscale gets a direct, full-quality path instead.

Design intent: *always works, sometimes badly.* Tailnet clients direct-play; everyone else
falls back to relay at roughly 1500 kbps SD with a server-side transcode.

Briefly configured tailnet-only earlier the same evening. That failed **closed** — a phone
off the tailnet could not see the libraries at all — which is unacceptable for a shared
server. The lesson is that "tailnet-only" and "shared with friends" are incompatible, and
the requirement, not the implementation, was wrong.

**Open decision — quality for off-tailnet users.** Relay is permanently SD for them. Giving
Ed full quality needs *both* of these, and neither works alone:

1. the gateway forwarding TCP/32400, and
2. a UFW rule allowing tcp/32400 (Plex is host-networked, so UFW's INPUT policy applies to
   it — unlike the container-published ports in §4.1)

Until both exist, Plex still advertises `68.59.111.64:32400` whenever remote access is on,
and every remote client wastes a connection attempt on it before falling back to relay.
Leaving that path advertised-but-broken is worse than either committing to it or accepting
relay-only.

### 4.3 Storage fabric ("core") — deferred, see §5

No change. The original justification for this section did not survive measurement.

### 4.4 DNS

AdGuard remains on mediahub-production, on host networking, listening on `*:53`, and it
does filter correctly. The server's own `resolv.conf` should point at it rather than
bypassing to `1.1.1.1`.

The single-point-of-failure in R7 is **known and accepted** — see section 6.

---

## 5. Sequencing

**Measurement precedes every change.** Approved 2026-08-22.

1. **Measure.** *(done 2026-08-22 — storage in §5.1, Moonlight in §5.2.)*
2. **Phase A — configuration only, $0.** *(applied 2026-08-22, except the gateway step.)*
   - **Done** — Plex: `customConnections="http://100.104.43.6:32400"`,
     `PublishServerOnPlexOnlineKey="0"`. Verified against the plex.tv resources API:
     `relay: False`, the `68.59.111.64` connection is gone, and `100.104.43.6:32400`
     is published with `local=False` for remote clients. Prefs backed up alongside the
     original; not committed here because it contains `PlexOnlineToken`.
   - **Done** — DNS: `/etc/resolv.conf` now `127.0.0.1` (AdGuard) then `1.1.1.1` as an
     availability fallback. Verified both directions: `doubleclick.net` -> `0.0.0.0`,
     `example.com` -> real address. Copy in `system/resolv.conf`.
   - **Outstanding** — remove the TCP/32400 forward on the gateway. Now cosmetic rather
     than functional: Plex no longer advertises or uses it, and UFW drops the port anyway.
     Must be done by hand in the gateway UI; its admin pages are JS-rendered with no
     postable form, and it 401s any request without a browser User-Agent.
3. **Phase B — storage fabric. Not justified. See §5.1.**
4. **Phase C — deferred.** See below.

### 5.1 Storage measurement, 2026-08-22 — and a corrected claim

The first draft of this document asserted that a NAS-hosted file "crosses `enp3s0` twice,"
giving "a practical ceiling of roughly half a gigabit." **That was wrong, and it was the
sole justification for Phase B.**

`enp3s0` negotiates **1000 Mb/s full duplex**. NAS→server is RX; server→client is TX.
They are independent budgets and do not contend. Nothing is halved.

Measured:

| Test | Result |
|---|---|
| Single sequential NFS read from NAS | 85.7 MB/s (686 Mbps) |
| Two parallel NFS reads | 60.2 + 58.3 = **118.5 MB/s aggregate (948 Mbps)** |
| Control: local disk read | 141 MB/s (proves `dd` was not the limiter) |

Aggregate *rose* with concurrency and stopped at wire rate. So the real shape is: one NFS
stream cannot saturate the link, and all NAS reads together share a ~948 Mbps ceiling.
That ceiling is roughly ten concurrent 4K remuxes, or dozens of the compressed files this
library actually favours. Real load is nowhere near it.

**Why Phase B is not merely unjustified but probably useless:** the ~948 Mbps cap could sit
on the server's 1 GbE port *or* the NAS's — both are 1 GbE, so this test cannot tell them
apart. Adding a faster NIC to the server changes nothing unless the NAS also exceeds
1 GbE. Confirm the NAS's link speed before this is ever reconsidered.

**Net effect: no hardware purchase is justified by any requirement in this document.**

### 5.2 Moonlight / access-layer measurement, 2026-08-22

Dangbei DBX3 Pro at 192.168.0.56, associated to the ARRIS 5 GHz radio (`TheDoghouse`,
BSSID `dc:a6:33:89:ca:16`), read directly off the device over ADB:

| Metric | Value | Comment |
|---|---|---|
| RSSI | **-52 dBm** | strong; anything above -65 is comfortable |
| Link speed | **702 Mbps** | near ceiling for AC2350 at 80 MHz / 2 streams |
| Band | 5240 MHz (5 GHz) | not stuck on 2.4 |
| Loss @ 1400B | **0%** | wired control also 0% |
| Jitter (mdev) | **1.9 ms** | wired control 1.4 ms |
| Added latency vs wired | **~3.8 ms** | 5.44 ms vs 1.66 ms average |

Moonlight needs roughly 20-30 Mbps at 1080p60, ~80 Mbps at 4K60. The link carries several
times that. **The access layer meets R5. No access point purchase is justified.**

Caveat worth re-testing if Moonlight ever feels bad: this measures the link, not airtime
contention with simultaneous Plex streaming to other wifi clients. The headroom is large
enough that contention is unlikely to be the first suspect.

### Phase C and its triggers

Full build: bridge the ARRIS, dedicated router, managed switch with VLANs, separate AP.
Not justified by R1–R7. It becomes justified only when one of these is true:

- The trust set opens up — real guests, or IoT that shouldn't reach the media server (invalidates R4)
- The outstanding Moonlight measurement shows the projector's wifi path is inadequate — note the fix then is an **access point**, not a router
- A second internet-facing service appears, making a DMZ worth building

### What is explicitly *not* being bought

- **Router / modem.** The SBG8300 forwards packets correctly; this was verified directly
  rather than inferred. No requirement it fails to meet.
- **Switch.** No VLAN requirement exists while R4 holds.

- **Network card.** Was provisionally justified; withdrawn after measurement (§5.1).

Nothing is being bought.

---

## 6. Known and accepted

**DNS is a single point of failure.** AdGuard runs only on mediahub-production. When that
host reboots, LAN clients lose DNS and the internet appears down even though routing is
unaffected — a partial violation of R3.

Accepted deliberately on 2026-08-22 rather than solved. Logged in `~/mediahub-cleanup.md`.
Failure mode is short, self-healing, and understood. Revisit when more household members
depend on the network, or if reboots become frequent.

---

## 7. Appendix: the diagnostic error this document exists to prevent

Four "independent" signals agreed that the gateway's port forwarding was broken:
an inbound path test, Plex falling back to relay, and an external port checker reporting
`closed` on two separate ports.

All four were the same measurement. Each asked only *can a connection complete from
outside*, and each was blind to the same thing: UFW drops with `DROP`, not `REJECT`, so no
RST is returned. "The gateway never forwarded the packet" and "the gateway forwarded it and
the host firewall silently discarded it" produce byte-identical results from outside.

The firewall log resolved it in one query — 441 distinct public source addresses arriving
on `enp3s0` across twelve days, including Plex's own reachability probes. The packets were
always arriving.

**Rules adopted:**

- Independent sources must have independent *failure modes*, not just different vantage
  points. Four tools sharing one blind spot are one source.
- Prefer an instrument inside the boundary over any number outside it.
- A negative result is not evidence until you have shown the instrument can produce a
  positive one.
