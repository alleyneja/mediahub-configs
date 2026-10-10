# Plex: LAN playback with the internet down

**Incident:** Oct 9 21:30 -> Oct 10 15:45 ISP outage (~18h). LAN stayed up, Plex
container stayed up and healthy, but Fire Stick and phone apps showed
"mediahub-prod offline / server unreachable". No playback request ever reached
the server (checked Plex log), so the failure was auth/discovery, not the server.

**Change (Oct 10):** set Plex preference `allowedNetworks=192.168.0.0/255.255.255.0`
("List of IP addresses and networks that are allowed without auth"). Applied live
via `PUT /:/prefs?allowedNetworks=...` (no restart). It is stored in
`Preferences.xml` on r9 (`/srv/docker/plex/Library/Application Support/Plex Media Server/`),
which is not in this repo because it holds the Plex token. Backup before the change:
`/srv/docker/plex/Preferences.xml.bak-20261010`.

**Verify:** `curl http://<r9-LAN-ip>:32400/library/sections` with no token returns 200.

**Not covered:** Tailscale clients (100.64.0.0/10) are not on the allow list; add
only if offline playback over Tailscale is wanted. Client apps may still want
plex.tv to sign in fresh; already-signed-in apps on the LAN are the target.
Needs a real test with WAN disconnected.

## Test result (Oct 10, Fire Stick 192.168.0.138, Plex app 2026.19.1)

Server side works offline: LAN no-token requests, GDM discovery and a 206 range
stream all pass; a non-allow-listed source (Tailscale) still gets 401.

**Client side does not.** With plex.tv blocked in AdGuard for the Fire Stick (and
NordVPN off), the Plex app sat on a black screen at launch. It queried only
`clients/features/luma/pubsub/cast-config/analytics.plex.tv`, never reached the
server (0 requests in the Plex log). `allowedNetworks` cannot help: the app
needs plex.tv before it ever contacts the server. A LAN-only fallback needs a
client that does not depend on plex.tv (Plex DLNA + VLC/Kodi, Plex Web by IP, or
direct NAS shares).

Gotchas found: the Fire Stick runs NordVPN (HDO Box / Stremio), whose DNS bypasses
AdGuard, so per-client AdGuard rules do nothing while it is connected. The Fire
Stick is now a persistent AdGuard client named "Fire Stick".

## Fallback that works offline: Plex DLNA + VLC (Oct 10)

`DlnaEnabled=1` set live via `PUT /:/prefs?DlnaEnabled=1` (no restart). Plex DLNA
server: SSDP on UDP 1900, device description + ContentDirectory on TCP 32469
(`http://192.168.0.22:32469/DeviceDescription.xml`). Verified from i7: SSDP discovery,
ContentDirectory browse (Video -> Movies -> All Movies), and VLC on the Fire Stick
(org.videolan.vlc) playing `http://192.168.0.22:32469/object/<id>/file.mkv` over
an established TCP session (PlaybackState=playing, picture on screen). Uses
LAN IP only; no plex.tv, DNS or internet. Watched state is not tracked.

In VLC on the Fire Stick: Browse -> Local Network -> mediahub-prod (UPnP). Not
yet verified: VLC's own UPnP browse UI (tested with a direct URL from the tree).
