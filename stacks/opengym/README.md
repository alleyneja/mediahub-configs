# openGym

Self-hosted gym & body-weight tracker. Multi-user, passkey auth, per-user isolated data.

- **URL:** https://mediahub-production.tail3b4ccf.ts.net:8443 (tailnet only)
- **Source:** pinned checkout at `/srv/docker/opengym/src` @ `c42ba6b`
- **Data:** `/srv/docker/opengym/data` — **back this up**, it holds every passkey

## No SSO — and it doesn't need it

There is no OIDC/OAuth/SAML/header-auth anywhere in the codebase. Authentik cannot
front this in any useful way: forward-auth would just mean logging in twice, with no
identity mapping between Authentik and openGym profiles.

Multi-user is native and sufficient: isolated per-user data, `INVITE_ONLY` gating,
`ADMIN_UIDS` for the admin dashboard, and session revocation via `POST /api/logout/all`.

Real SSO would mean forking the 554-line backend to swap the WebAuthn routes for an
OIDC code flow. Note the AGPL-3.0: a modified version served to others obligates
publishing the source.

## Provenance — read before updating

Upstream `github.com/DuarteSantos8/openGym` is **deleted (404)**. This runs a re-upload,
`github.com/arvids-unavailable/openGym`, whose commits are titled "asd". Its prebuilt
images `ghcr.io/duartesantos8/opengym-{api,web}` are **not anonymously pullable (403)**,
so `docker compose pull` cannot work — we build from source at a pinned commit.

**The repo ships live secrets in a committed `data/` directory**, and the code only
generates new ones if those files are absent. Cloning and running as-shipped gives you a
session-signing key that is public on GitHub — a forged `gymsid` cookie authenticates as
the committed user with no passkey. Verified: forged cookie returned HTTP 200 against an
as-shipped instance; no-cookie, tampered-signature, and unknown-uid controls all 401'd.

`/srv/docker/opengym/src` has `data/` deleted. **Any re-clone must delete it again.**
Verify after any source update:

    sudo head -c 16 /srv/docker/opengym/data/secret   # must NOT be 1ba7c4c51338dda8

## Two upstream defects worked around

1. `docker-compose.yml` builds `web` from `web/Dockerfile`, but `web/` holds only
   `nginx.conf` — the real multi-stage Dockerfile is at the repo root. Ours points at
   `Dockerfile`. As shipped, `docker compose up --build` fails outright.
2. Committed `data/` secrets, above.

## Why :8443 and not Caddy

Passkeys (WebAuthn) require a certificate the *device* already trusts. Every `.lan` host
here uses Caddy's `tls internal` CA, and a browser will refuse to show the passkey prompt
on a connection it does not trust — with no click-through. Phones would each need the
Caddy root CA installed and, on iOS, separately set to full trust.

Tailscale issues a real Let's Encrypt cert for the ts.net name (verified: issuer
`C=US, O=Let's Encrypt, CN=YE2`), which every phone trusts with nothing installed.

Port 8443 rather than 443 because **Caddy owns `0.0.0.0:443`**, which covers the tailnet
interface — `tailscale serve --https=443` silently receives no traffic there. This is a
deliberate exception to the ":8443 is always wrong" rule in
`docs/8443-port-mismatch.md`: here Tailscale, not Caddy, terminates TLS.

    sudo tailscale serve --bg --https=8443 http://127.0.0.1:8085

Funnel is **off** — tailnet only. Verified not reachable via the LAN IP.

## RP_ID is permanent; ORIGIN is not

`RP_ID` binds every passkey. Changing it invalidates all of them. `ORIGIN` (including the
port) is only checked at assertion time, so moving off :8443 later — e.g. fronting the
ts.net name with Caddy using a `tailscale cert` — keeps existing passkeys valid, as long
as `RP_ID` stays the same hostname and `ORIGIN` is updated to match.

## First-run setup

1. Register your profile at the URL above.
2. Read your id: `sudo python3 -c "import json;print([u['id'] for u in json.load(open('/srv/docker/opengym/data/db.json'))['users']])"`
3. Put it in `ADMIN_UIDS` in `.env`, set `INVITE_ONLY=1`, then `docker compose up -d api`.
4. Generate invite codes from the admin dashboard for Mafe / your brother.

Signup is open until step 3. Do it before sharing the URL.

## Backup

    tar czf opengym-$(date +%F).tar.gz -C /srv/docker/opengym data
