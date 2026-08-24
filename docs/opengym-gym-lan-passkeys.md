# openGym on gym.lan: what passkeys cost us

Decided 2026-08-23, moving openGym off `tailscale serve` and onto Caddy at `gym.lan`.
Written down because every constraint below is either irreversible or fails silently.

## The decision

openGym is served by Caddy with `tls internal`, like every other `.lan` host, so it sits
in the same self-renewing certificate loop as the rest of the stack. The alternative was
`tailscale serve` with a Let's Encrypt cert, which needs nothing installed on a device.
We chose Caddy because Jay's and Maria's phones already trust the internal CA, and
uniformity was worth more than onboarding convenience for a two-person service.

This is a real trade, not a free win. The costs are below.

## Passkeys are the only way in

There are exactly two auth routes on the server, `register` and `login`, both WebAuthn.
**No password, no email, no reset link, no recovery.** Guest mode is not an account — it
is browser-local storage that never reaches the server.

## One account holds exactly one passkey, permanently

There is no endpoint to add a second device. `POST /api/register/verify` always runs
`db.users.push(user)`, so registering again creates a *new account* rather than attaching
a credential to an existing one. The admin dashboard can list, disable, and manage
invites — it **cannot** reset a credential.

Consequence: lose the passkey, lose the account and all its history, with no recovery
path for anyone including the admin. In practice iCloud Keychain and Google Password
Manager sync passkeys, so a replacement phone restores it. A device-bound passkey, or a
device with keychain sync off, has no safety net.

**Therefore `RP_ID=gym.lan` is now effectively permanent.** Changing it does not merely
invalidate passkeys — because credentials cannot be re-attached, everyone would have to
register as a brand-new account and abandon their history. The cheap moment to change
this was before anyone registered, and it has passed.

## A new device must install AND trust the Caddy root CA

Not just install — iOS needs the profile added under Settings → General → VPN & Device
Management, *and then separately enabled* under General → About → Certificate Trust
Settings. Missing the second step is the usual failure.

**The failure mode is quiet.** An untrusted cert shows a full-page browser warning that
can be clicked past ("Advanced → proceed"), after which the site loads and looks
completely normal — but browsers refuse WebAuthn on a connection with certificate errors,
so the passkey prompt simply never appears. Nothing says "certificate". The sign-in
button just does nothing.

The root CA is at `/srv/docker/caddy/caddy-root.crt`, valid until **2036-01-19**. It is
not served anywhere; distributing it to a new device is a manual step.

## Installing that CA is a real security grant

A trusted root can mint a valid certificate for *any* domain — a bank, an email provider —
for the device that trusts it. The private key lives on mediahub. For Jay's and Maria's
own devices on Jay's own server this is an accepted risk. It is a meaningfully larger ask
for anyone else's personal phone, and it applies to all of that device's traffic, not
just to this service.

## Guest mode is the escape hatch for anyone without the CA

Guest mode works fine on an untrusted cert (click past the warning); it is pure
`localStorage` and does not involve the server. Caveats:

- Data lives only in that browser. Clearing site data wipes it, and there is **no copy on
  the server** — nothing to restore.
- The app's own code notes iOS evicts localStorage under storage pressure. Not durable on
  an iPhone unless added to the home screen.
- No sync. Phone and laptop are two unconnected logs.

**Guest data does migrate.** If a guest later installs the CA and registers on the same
browser, the client pushes local state up: a new account has no server-side state, so
`pullState()` falls through to `pushState()` rather than overwriting. They do not restart
from zero.

## gym.lan still requires the tailnet

The AdGuard rewrite points `gym.lan` at `100.104.43.6` — the Tailscale IP, matching all
34 other `.lan` names. So access requires Tailscale *and* the CA. Tailscale remains the
primary gate, per `docs/network-architecture.md`.

## What the migration cost

Jay's original account (`N4S91qKxLNW34FgR`, "Jay A.") was registered under
`RP_ID=mediahub-production.tail3b4ccf.ts.net` and its passkey died with the switch. The
account was cleared from `db.json` so it would not linger as an unusable orphan.

Nothing of value was lost: that account held zero workouts and zero bodyweight entries —
one routine plus display settings, from an evening of browsing the exercise archive. The
orphaned state file and the pre-migration backup tarball were both **deleted on 2026-08-24**
at Jay's request; a transplant onto the new uid was judged not worth the sync race for one
routine and a colour preference.

Current account: `o1Hhgi8Uz2zeJI6M` ("Jay A."), admin via `ADMIN_UIDS`, passkey held in
iCloud Keychain. A copy was placed in Bitwarden but does not currently sign in, so iCloud
is the live credential — and note Vaultwarden itself has no scheduled backup.

## Reverting

`ORIGIN` can change freely (it is only checked at assertion time); `RP_ID` cannot.
Going back to Tailscale would mean `RP_ID` reverts too, so it costs every account again.
Treat this as one-way.
