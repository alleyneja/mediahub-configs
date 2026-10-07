# New user / new device onboarding

Everything a new person or new device needs before `.lan` sites work. There are **three**
independent layers, and all three must be done. A failure in any one looks like "the site
doesn't open", so check them in this order.

| # | Layer | Symptom if missing |
|---|---|---|
| 1 | Tailscale (joined to the right tailnet) | `.lan` names don't resolve at all |
| 2 | DNS (comes with Tailscale, see below) | `could not resolve` / site not found |
| 3 | **Caddy root CA installed AND trusted** | Name resolves, but a certificate warning, or the page silently never loads (iOS, apps) |

## 1. Tailscale

Invite by email from admin.tailscale.com > Users, accept on the device, then **sign out and
back in** in the Tailscale app so it prompts for the tailnet; pick `alleynejacob`. Do not use
"share node". Free plan allows 3 users. Details and traps: Jay's Tailscale multi-user notes.

## 2. DNS

Nothing to do per device. The Tailscale admin console already points the `lan` split route
and the global list at AdGuard (production `100.104.43.6`, x51 replica, then public fallback).
See `dns-resilience.md`. Quick check on the device: `plex.lan` should resolve to `100.104.43.6`.

## 3. Trust the Caddy root CA (the step that gets forgotten)

All `.lan` sites use Caddy's `tls internal`, so browsers and apps only trust them once the
**Caddy Local Authority root certificate** is installed and trusted on the device.

- File on production: `/srv/docker/caddy/caddy-root.crt`
  (subject `Caddy Local Authority - 2026 ECC Root`, valid until **2036-01-19**).
  Regenerate/copy from `/srv/docker/caddy/data/caddy/pki/authorities/local/root.crt` if lost.
  It is a public certificate (no private key), safe to send by message or email.
- **Distribution is deliberately manual:** Jay keeps a copy in Nextcloud and texts it to the new
  user. It is not hosted anywhere permanent, by choice (few users, one-time step per device).

### Per platform

- **iPhone / iPad:** open the `.crt` > allow the profile download > Settings > General > VPN &
  Device Management > install the profile > **then** Settings > General > About >
  **Certificate Trust Settings** > turn ON full trust for "Caddy Local Authority". Installing
  alone is not enough; skipping the trust toggle is the usual mistake (Maria, 2026-10-05).
- **Android:** Settings > Security > Encryption & credentials > Install a certificate > CA
  certificate. Note: many apps ignore user-installed CAs; browsers (Chrome) honour it.
- **Windows:** double-click the `.crt` > Install Certificate > Local Machine > place in
  "Trusted Root Certification Authorities". Firefox keeps its own store (Settings > Privacy &
  Security > Certificates > Import).
- **macOS:** open in Keychain Access (System keychain), double-click the cert > Trust >
  "Always Trust".
- **Linux:** copy to `/usr/local/share/ca-certificates/caddy-root.crt` then
  `sudo update-ca-certificates` (browsers with their own store need a separate import).

### Verifying it worked

Open `https://plex.lan` (or `https://stirling.lan`) in the device's browser: padlock, no
warning. If DNS resolves (layer 2) but you still get a warning or a blank load, it is this step.

### Passkeys

`gym.lan` (openGym) is passkey-only and WebAuthn refuses untrusted certificates, so the CA
must be trusted there too. See `opengym-gym-lan-passkeys.md`.
