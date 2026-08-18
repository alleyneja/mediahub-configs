# RomM ← Authentik OIDC (per-user saves for phone play)

**Status:** configured and verified up to the redirect, 2026-08-18. The final
browser login is Jay's to do.

## Why

RomM's EmulatorJS runs client-side in the browser, so several people can play at once
with no encode and no one-session limit — unlike Sunshine. `romm.saves` and
`romm.states` both key on `user_id`, so separate logins are what give Jay and Mafe
independent saves. Sharing the single `admin` account would defeat the whole point.

**RomM saves are separate from `/mnt/internal/arcade/saves/`.** A GBA run on a phone
does not continue on the projector.

## Shape of it

- Authentik provider + application come from a **blueprint**, not UI clicks:
  `stacks/authentik/blueprints/romm-oidc.yaml`, mounted at `/blueprints/custom`
  on both `authentik-server` and `authentik-worker`. It re-applies itself if
  authentik is rebuilt.
- Credentials live in the **authentik stack.env** as `ROMM_OIDC_CLIENT_ID` /
  `ROMM_OIDC_CLIENT_SECRET`, passed into both containers and pulled into the
  blueprint with `!Env`. mediahub-configs is public; the blueprint carries no secret.
- RomM reads the same two values from its own stack.env.

Apply by hand after editing:

    docker exec authentik-worker ak apply_blueprint /blueprints/custom/romm-oidc.yaml

## Four traps, all hit or avoided deliberately

1. **`!Env [VAR]` crashes.** authentik's Env tag reads `node.value[1]` for the
   default, so a single-element list raises `IndexError: list index out of range`
   and the whole blueprint fails to apply. Use the scalar form `!Env VAR`, or give
   a default: `!Env [VAR, fallback]`.
2. **Caddy's internal CA.** `*.lan` is signed by Caddy's own CA, which the RomM
   container does not trust. authlib fetches the discovery document over TLS, so
   without `OIDC_TLS_CACERTFILE` the login dies before it ever reaches Authentik.
   Caddy's root is bind-mounted read-only:
   `/srv/docker/caddy/data/caddy/pki/authorities/local/root.crt` → `/certs/caddy-root.crt`.
3. **`email_verified`.** RomM rejects the login with HTTP 400 if the provider
   advertises `email_verified` in `claims_supported` and does not return `true`.
   Authentik advertises it, and its default email scope mapping hardcodes
   `"email_verified": True` — so this passes. It would not with a provider that
   reports real verification state.
4. **`:8443` vs 443.** Not present here — both self-URLs are plain `https://` on 443.

## Roles

With `OIDC_CLAIM_ROLES` unset, every new OIDC user is created as **VIEWER**. That is
fine for play: outside kiosk mode VIEWER resolves to `WRITE_SCOPES`, which includes
`assets.write` — saves and states work. Only ROM/platform editing and admin need more.

RomM matches users **by email**. Jay's Authentik account is `alleynejacob@gmail.com`
and RomM's local `admin` is `admin@mediahub.com`, so his first SSO login creates a
*second*, non-admin RomM account. Options, his call:

- promote the new `alleyneja` account to ADMIN from the local admin (one click), or
- change `admin`'s email to his so SSO maps onto the existing account instead.

Local username/password login is deliberately left enabled (`DISABLE_USERPASS_LOGIN`
unset) so `admin` remains break-glass.

## Verified

- Provider + application exist, `client_type=confidential`, signing key attached,
  redirect URI `https://romm.lan/api/oauth/openid` strict.
- `/api/heartbeat` → `OIDC.ENABLED true`, `PROVIDER authentik`.
- `GET /api/login/openid` → **302** to `https://auth.lan/application/o/authorize/`
  with the right `client_id`, `redirect_uri` and `scope=openid profile email`.
  That 302 can only be built from a successfully fetched discovery document, so it
  also proves trap 2 is solved.
- Every other Authentik-backed service still healthy after the restarts.

**Not verified:** an actual browser sign-in, and Mafe's first login. Both need a human.
