# Editing the Caddyfile can silently sever its bind mount

## Symptom

You edit `/srv/docker/caddy/Caddyfile`, add a vhost, run `caddy reload`, and the reload
*succeeds* — but the new site fails TLS with:

```
TLSv1.3 (IN), TLS alert, internal error (592)
OpenSSL/3.0.13: error:0A000438:SSL routines::tlsv1 alert internal error
```

Existing sites keep working normally. Caddy's log shows:

```
{"level":"info","msg":"config is unchanged"}
```

## Cause

The Caddyfile is a **single-file bind mount**, read-only:

```
/srv/docker/caddy/Caddyfile -> /etc/caddy/Caddyfile   (RW=false, mode=ro)
```

Docker binds a single-file mount to the file's **inode**, not its path. Any editor that
saves by writing a temporary file and renaming it over the original — which is most of
them, including `sed -i`, `vim` with default settings, and most agentic editing tools —
creates a **new inode**. The host path now points at the new inode; the container is
still pinned to the old one.

The result is that the host file is correct, the container still reads the old content,
and Caddy honestly reports the config as unchanged. The TLS "internal error" is Caddy
being asked for a certificate for a hostname that, as far as it knows, was never
configured.

Confirm with:

```bash
stat -c '%i %s' /srv/docker/caddy/Caddyfile
docker exec caddy stat -c '%i %s' /etc/caddy/Caddyfile
```

Different inodes means the mount is severed.

## Fix

The mount is read-only, so writing through with `docker exec` does not work
(`Read-only file system`). Restart the container so the bind mount re-resolves to the
current path:

```bash
docker restart caddy
```

Verify the inodes match afterward, and that the container can see the change:

```bash
docker exec caddy grep -c "yournewsite.lan" /etc/caddy/Caddyfile
```

A restart blips every reverse-proxied service for a second or two. Plex is unaffected —
it runs on host networking.

## Avoiding it

Append in place rather than rewriting the file, which keeps the inode:

```bash
cat >> /srv/docker/caddy/Caddyfile <<'EOF'

yournewsite.lan {
    tls internal
    reverse_proxy yourcontainer:PORT
}
EOF
```

## Verification that actually proves it worked

`caddy reload` exiting 0 is **not** evidence — it exits 0 while reporting "config is
unchanged". Check that the container sees the change, then request the site:

```bash
docker exec caddy grep -c "yournewsite.lan" /etc/caddy/Caddyfile   # expect >= 1
curl -s --cacert /srv/docker/caddy/caddy-root.crt \
  --resolve yournewsite.lan:443:127.0.0.1 \
  https://yournewsite.lan/ -o /dev/null -w 'HTTP %{http_code} verify=%{ssl_verify_result}\n'
```

Expect `HTTP 200 verify=0`. A `verify` value other than `0` means the certificate did
not validate against the internal CA.

## Scope

This affects **single-file** bind mounts only. Directory bind mounts — such as
Homepage's `/srv/docker/homepage/config` — are unaffected, because the container
resolves paths inside the directory at access time rather than pinning an inode.

Discovered 2026-08-12 while adding `mealie.lan`.
