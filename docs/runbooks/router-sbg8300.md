# Runbook: reading / editing the ARRIS SBG8300 gateway (192.168.0.1)

Written 2026-10-11 (issue #22). The gateway is router, NAT, DHCP, firewall and Wi-Fi in one box.
Credentials: `scripts/modem.env` (gitignored, mode 600) — see `docs/netmon.md`. Never put them in this file.

## Rules before you touch it
1. **One admin session only.** A login while someone else is in answers `{"session_overtake":true}` and every page is 401
   until you confirm the takeover (below). That kicks the other user out — ask first if a human may be in the UI.
2. **netmon uses the same session at :00 and :30** (`*/30` cron). Do your work between runs, and always log out.
3. **Bad logins can lock the gateway out.** A wrong password returns HTTP 409. Stop at the first one; do not loop.
4. Read first, write second. Reads are `GET`; every change is an `actionHandler/ajaxSet_*.php` call and survives only if the
   box accepts it — take a copy of the relevant page before and after.

## Login flow (works with plain urllib/curl; the page's JS encryption is off)
1. `GET /login.php` (sets the session cookie).
2. `PUT /actionHandler/ajaxSet_login.php` with JSON `{"username": MODEM_USER, "password": MODEM_PASS}`.
   - Empty body = logged in. 409 = credentials rejected.
   - `{"session_overtake":true}` = someone (or a stale session) is already in.
3. If overtaken: `PUT /actionHandler/ajaxSet_SessionActive.php` with `{"session_takeover": true}`.
4. Take the `X-CSRF-Token` response header from step 2 (or 3) and send it as `X-CSRF-Token` on every later request.
5. Do the work. **Always** finish with `PUT /actionHandler/ajaxSet_logout.php` (empty body) in a `finally`.

Reference implementation: `scripts/netmon.py` `modem_stats()` (read-only DOCSIS tables via `wan.php`).

## Where things are (menu labels != the names you would guess)
| Want | Page |
|---|---|
| Port forwards (incl. UPnP/NAT-PMP auto entries) | `port_forwarding.php` (rows: "Service Name / Type / Internal / External / Server IPv4") |
| DMZ | `dmz.php` (initial state is in the page JS: `state: false ? "on" : "off"`) |
| Admin password | `account_management.php` |
| Firewall-ish | `alg.php`, `port_triggering.php`, `managed_services.php`, `managed_sites.php`, `managed_devices.php` |
| DOCSIS signal | `wan.php` (`let channelData = {...}`) |
| Reboot / restore | `restore_reboot.php` |

**There is no "Remote Management" / "WAN-side admin" setting on this firmware.** `remote_management.php` returns 403.
The menu has no such entry; exposure to the internet is decided by port forwards and DMZ only.
Nonexistent pages return a 33 KB login-page fallback with HTTP 200 — a `200` is not proof a page exists; 401/403 is.

## State found 2026-10-11 (read-only)
- DMZ: off.
- 281 forward rules: 275 are `tailscale-portmap` (UPnP/NAT-PMP, from Tailscale clients: .206, .131, .21, .22), plus six static:
  `ssh 22 -> .23`, `http 9999 -> .23`, `https 9443 -> .23` (the UGREEN NAS), `MinecraftBedrockPublic 25500-25502 -> .22`,
  `Plex 32400 -> .22`, `tailscaleMediahubStatic 41641 -> .21`.
- The NAS SSH + web UI forwards are the real exposure; see the issue filed from #22.

## Deleting / changing a port forward (learned 2026-10-11)
- Delete: `DELETE /actionHandler/ajax_port_forwarding.php` with JSON `{"id": N}` (N from the row's `port_forwarding_edit.php?id=N`).
  Toggle active: `POST` same URL `{"id": N, "active": true|false}`. Delete from the highest id down and re-read the table after each.
- **Verify by re-reading the table ~30 s later**, not from the DELETE's "ok". Rules named `ssh`/`http`/`https` -> 192.168.0.23 were
  deleted and **reappeared within a minute with new ids (449, 501)**: the NAS itself re-creates them (UPnP / its remote-access
  feature). Deleting them on the router is futile until that is turned off on the NAS.

## UPnP view of the same table (the useful one for "who made this rule")
`upnpc -u http://192.168.0.1:49152/IGDdevicedesc_brlan0.xml -l` (package `miniupnpc`, no auth, read-only) lists every
UPnP-created mapping with its description and lease. Gateway's own UI does not say which rules are UPnP vs manual and its log is empty.
2026-10-11: `https 9443`, `http 9999`, `ssh 22` -> 192.168.0.23 are **UPnP mappings, lease 604800 s (7 d)**, renewed/re-added
within ~1 min of deletion. UGREEN Remote Access and DDNS are OFF in UGOS, so the source is something else on the NAS (or a
container on it). Router has no UPnP on/off page on this firmware.

## RESOLVED 2026-10-11 (issue #77): NAS internet exposure
Cause: UGOS **Control Panel > Connection & Access > Device Connection > Remote access > "Enable auto router port mapping"** was ON
(independent of UGREENlink and DDNS, which were off). It UPnP-mapped ssh 22 / http 9999 / https 9443 to the internet with a 7-day
lease and re-added them within ~1 min of router-side deletion. Unticking + saving removed the mappings; confirmed gone in both the
UPnP listing and the router table after 90 s. Remaining deliberate forwards: Minecraft 25500-25502 and Plex 32400 -> r9,
Tailscale 41641 -> mediahub. NAS UI/NFS/Plex unaffected. Re-check any time: `upnpc -u http://192.168.0.1:49152/IGDdevicedesc_brlan0.xml -l | grep 192.168.0.23`
(expect no output). Same trap likely applies to any new UGREEN NAS or UGOS reinstall: untick that box on first setup.
