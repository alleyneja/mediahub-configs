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
