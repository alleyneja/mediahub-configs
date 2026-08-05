# Calibre-Web — "Error Downloading Cover" was a CA trust-store mistake

## 2026-08-04 — covers and every metadata provider failing

**Symptom:** saving metadata in Calibre-Web showed `Error Downloading Cover`.

```
ERROR {cps.helper:1071} Cover Download Error ... host='cdn.kobo.com'
  SSLCertVerificationError: unable to get local issuer certificate
WARN {cps.editbooks:894} [edit_book] cover save failed book_id=90
```

The visible error was only the tip. **Every** metadata provider was failing the
same way and had been for months — Google Books, Amazon, Amazon JP, Kobo,
Douban, ibdb, DNB, Litres, lubimyczytac. They log at WARN and fall through
silently, so searches just returned thin results instead of an error.

**Cause:** the container set

```yaml
REQUESTS_CA_BUNDLE: /etc/ssl/certs/caddy-root.crt
```

`REQUESTS_CA_BUNDLE` **replaces** Python's trust store, it does not extend it.
That file holds exactly one certificate — `CN = Caddy Local Authority - 2026 ECC
Root` — so the container trusted Caddy and no public CA at all. All 146 public
roots were gone.

Proven in the running container, same second, only the bundle swapped:

| bundle | cdn.kobo.com | googleapis.com |
|---|---|---|
| `caddy-root.crt` (1 cert) | SSLCertVerificationError | SSLCertVerificationError |
| host bundle (123 certs) | HTTP 404 (TLS fine) | HTTP 429 (TLS fine) |

## Why it happened — the Node/Python asymmetry

Commit `31bfa7b` (2026-05-17) deliberately distributed Caddy's internal CA so
containers could speak HTTPS to `.lan` services. The *intent was correct*. The
mechanism differs by runtime, and that is the trap:

| container | variable | semantics |
|---|---|---|
| audiobookshelf | `NODE_EXTRA_CA_CERTS` | **adds** to the trust store ✓ |
| immich-server | `NODE_EXTRA_CA_CERTS` | **adds** ✓ |
| calibre-web-automated | `REQUESTS_CA_BUNDLE` | **replaces** ✗ |
| nextcloud | `REQUESTS_CA_BUNDLE` | **replaces** ✗ (see below) |

Node has an additive variable. **Python has no additive equivalent** — the only
way to trust an extra CA is to supply a bundle containing both.

This also disproves the natural assumption that "being behind Caddy breaks
outbound HTTPS". audiobookshelf and immich sit behind Caddy, trust the Caddy
root, and download from the internet fine.

## Applied

The host trust store already contains **both** — public roots from the
`ca-certificates` package, plus the Caddy root that `setup.sh` installs into
`/usr/local/share/ca-certificates/`. So no new file needs building or
maintaining; mount the host bundle:

```yaml
environment:
  REQUESTS_CA_BUNDLE: /etc/ssl/certs/ca-bundle-with-caddy.crt
volumes:
  # was: /srv/docker/caddy/caddy-root.crt:/etc/ssl/certs/caddy-root.crt:ro
  - /etc/ssl/certs/ca-certificates.crt:/etc/ssl/certs/ca-bundle-with-caddy.crt:ro
```

It stays current automatically: `apt` maintains the public roots, `setup.sh`
maintains the Caddy one.

**Verified after**, inside the running container:

```
cover CDN (kobo)           OK   HTTP 404
Google Books               OK   HTTP 429
Amazon                     OK   HTTP 200
internal calibre-web.lan   OK   HTTP 302   <- Caddy cert still verifies
```

And the exact cover URL from the original error now returns
`HTTP 200 | image/jpeg | 314248 bytes`. Library still reports 53 books;
Calibre-Web still serves behind Caddy and redirects to `/login`.

## Unrelated stale entry found nearby

`extra_hosts: auth.lan:172.18.0.28` pins `auth.lan` to an IP that now belongs to
**gluetun**. Caddy is `172.18.0.200`. Nothing depends on it today — Calibre-Web's
Authentik config reaches `http://authentik-server:9000` over plain HTTP
server-side, and the one HTTPS URL (`authorize`) is visited by the user's
browser, not the container. Left in place; noted so it is not mistaken for
working configuration.

## Still to check

`nextcloud` carries the same `REQUESTS_CA_BUNDLE` override. It is PHP, which
does not read that variable, so it is probably inert — but unverified.
