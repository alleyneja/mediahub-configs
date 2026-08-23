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
| R2 | Plex, RomM / arcade downloads, and all remote admin are **tailnet-only** | stated |
| R3 | Household internet must survive mediahub-production being down | stated |
| R4 | Trust set is closed and known: Jay, Mafe, brother, household devices. No public wifi guests, no strangers on the LAN | stated |
| R5 | Local Moonlight: ceiling-mounted Dangbei projector, wifi, ~40 ft, one interior wall | stated |
| R6 | Storage traffic and client traffic currently share one 1 Gb NIC | measured |
| R7 | AdGuard is functional but is a single point of failure for all LAN DNS | measured |

### Non-requirements

Recorded deliberately, so they are not re-litigated:

- **Guest network isolation.** R4 says the trust set is closed. Revisit if that changes.
- **IoT segmentation.** No untrusted IoT of consequence today.
- **Public Plex access.** R2 is explicit. Plex is tailnet-only *by policy*, not by accident.

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
CORE          does not exist.
              Server<->NAS NFS traffic shares the client path.

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
| Access | R5 is the only hard performance floor. **Unmeasured.** | **Unproven** |
| Core | R6 has no configuration workaround. | **Genuine gap** |

---

## 4. Target architecture

### 4.1 Edge

One forward, for Pterodactyl. Nothing else.

The TCP/32400 forward is **removed**. It contradicts R2, and it was drawing scans from
441 distinct public addresses to no benefit. UFW's default-deny was already enforcing the
correct policy; the gateway forward was the misconfiguration.

### 4.2 Plex path

Plex stops advertising a WAN connection and is reached over the tailnet.

The observed relay fallback — remote playback capped at ~1500 kbps, SD, server-side
transcode — was caused by Plex advertising a WAN address that dies at the host firewall,
while a working tailnet route to the same server sat unused. This is a Plex configuration
defect. No hardware is involved.

### 4.3 Storage fabric ("core")

A dedicated point-to-point link between mediahub-production and the NAS, on its own
subnet, carrying only NFS.

Today a client playing a NAS-hosted file moves that data across `enp3s0` twice: inbound
from the NAS, outbound to the client. The practical ceiling is roughly half a gigabit
before contention. A second NIC and a direct cable removes storage traffic from the client
path entirely — no switch, no VLAN, no router.

### 4.4 DNS

AdGuard remains on mediahub-production, on host networking, listening on `*:53`, and it
does filter correctly. The server's own `resolv.conf` should point at it rather than
bypassing to `1.1.1.1`.

The single-point-of-failure in R7 is **known and accepted** — see section 6.

---

## 5. Sequencing

**Measurement precedes every change.** Approved 2026-08-22.

1. **Measure.** Moonlight path quality at the projector (jitter and airtime under load,
   with Plex streaming as competing traffic) and the NAS double-traffic penalty on
   `enp3s0`. Establish numbers before touching configuration.
2. **Phase A — configuration only, $0.** Remove the 32400 forward. Give Plex a tailnet
   connection URL and disable its remote-access advertisement. Point the server's resolver
   at AdGuard. Verify the Pterodactyl forward.
3. **Phase B — storage fabric, ~$25–60.** Second NIC plus a direct cable to the NAS, if
   and only if the step 1 measurement confirms the contention penalty.
4. **Phase C — deferred.** See below.

### Phase C and its triggers

Full build: bridge the ARRIS, dedicated router, managed switch with VLANs, separate AP.
Not justified by R1–R7. It becomes justified only when one of these is true:

- The trust set opens up — real guests, or IoT that shouldn't reach the media server (invalidates R4)
- Step 1 shows the projector's wifi path is inadequate for Moonlight — note the fix then is an **access point**, not a router
- A second internet-facing service appears, making a DMZ worth building

### What is explicitly *not* being bought

- **Router / modem.** The SBG8300 forwards packets correctly; this was verified directly
  rather than inferred. No requirement it fails to meet.
- **Switch.** No VLAN requirement exists while R4 holds.

The only hardware pointing at a purchase is a network card.

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
