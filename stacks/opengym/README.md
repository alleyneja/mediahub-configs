# openGym

Self-hosted gym & body-weight tracker. Multi-user, passkey auth, per-user isolated data.

- **URL:** https://gym.lan (tailnet + Caddy internal CA — see docs/opengym-gym-lan-passkeys.md)
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

## Served by Caddy at gym.lan

Moved off `tailscale serve` on 2026-08-23. Caddy serves it with `tls internal` like every
other `.lan` host, so its certificate renews in the same closed loop as the rest of the
stack — Caddy is both issuer and renewer, and the leaf certs rotate every 12 hours
unattended.

The trade: passkeys need a cert the *device* trusts, so **every device must install and
trust the Caddy root CA** (`/srv/docker/caddy/caddy-root.crt`, valid to 2036-01-19), and
the failure mode when it hasn't is silent. Jay's and Maria's phones already trust it.

`reverse_proxy opengym-web:80` over `mediahub_internal` — Caddy is on that network only,
which is why `opengym-web` joins it. The `127.0.0.1:8085` publish is kept for host-side
debugging, not as a front door. `gym.lan` resolves to `100.104.43.6` (Tailscale), so the
tailnet is still the outer gate.

**Read `docs/opengym-gym-lan-passkeys.md` before changing any of this.** Passkeys are the
only sign-in, one account holds exactly one passkey forever, and `RP_ID` is now
effectively permanent.

## RP_ID is permanent; ORIGIN is not

`RP_ID` binds every passkey, and because there is no add-a-device endpoint, changing it
forces everyone into brand-new accounts and abandons their history. `ORIGIN` is only
checked at assertion time and can change freely.

## First-run setup

1. Register your profile at the URL above.
2. Read your id: `sudo python3 -c "import json;print([u['id'] for u in json.load(open('/srv/docker/opengym/data/db.json'))['users']])"`
3. Put it in `ADMIN_UIDS` in `.env`, set `INVITE_ONLY=1`, then `docker compose up -d api`.
4. Generate invite codes from the admin dashboard for Mafe / your brother.

Signup is open until step 3. Do it before sharing the URL.

## Backup

    tar czf opengym-$(date +%F).tar.gz -C /srv/docker/opengym data
