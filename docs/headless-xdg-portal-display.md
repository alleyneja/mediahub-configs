# xdg-desktop-portal on a headless X session — `cannot open display`

## 2026-08-15 — every Qt app on `:1` was stalling 120 seconds before it painted

**Symptom:** PCSX2 and Dolphin launched on display `:1` (the headless Sunshine
session), took GPU memory, mapped only their 1x1 Qt helper windows, and never
painted a real window for two full minutes. Their logs showed:

```
Call to org.freedesktop.portal.Settings.ReadAll failed
  QDBusError("org.freedesktop.DBus.Error.NoReply", ...)
Call for getting org.freedesktop.portal.FileChooser version failed
  QDBusError("org.freedesktop.DBus.Error.TimedOut",
             "Failed to activate service 'org.freedesktop.portal.Desktop':
              timed out (service_start_timeout=120000ms)")
```

**This was previously recorded as a startup race condition. It is not.** It fails
deterministically, every launch, and had been doing so since the headless session
was first built on 2026-08-14.

**Cause:** both portal services were dead:

```
xdg-desktop-portal-gtk.service    loaded failed failed
xdg-desktop-portal.service        loaded failed failed
xdg-document-portal.service       loaded active running
```

`xdg-desktop-portal-gtk` **is itself a GTK application and needs an X display to
start.** The headless server runs on `:1` with a non-default auth file
(`/etc/X11/headless.xauth`), and the systemd `--user` manager inherits neither
`DISPLAY` nor `XAUTHORITY`. So the backend died instantly:

```
xdg-desktop-portal-gtk[119437]: cannot open display:
```

That cascades. `xdg-desktop-portal` waits 90s for its gtk implementation, times
out, and fails. Every Qt app then blocks the full 120s `service_start_timeout`
trying to activate a portal that cannot exist, before finally giving up and
painting.

**The `portals.conf` fix from 2026-08-14 was necessary but not sufficient.**
Forcing `default=gtk` is still correct — the gnome backend never answers without
a GNOME session — but it only selects a backend, it cannot give that backend a
display.

## Fix

`~/.config/systemd/user/xdg-desktop-portal-gtk.service.d/headless-display.conf`
(mirrored in this repo at `systemd/user/...`):

```ini
[Service]
Environment=DISPLAY=:1
Environment=XAUTHORITY=/etc/X11/headless.xauth
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user reset-failed xdg-desktop-portal.service xdg-desktop-portal-gtk.service
systemctl --user restart xdg-desktop-portal-gtk.service
systemctl --user start xdg-desktop-portal.service
```

Both report `active`, and Qt launch logs come back clean with no portal errors.

Scoped to a drop-in on the one service rather than
`systemctl --user set-environment DISPLAY=:1`, so nothing else in the user
manager inherits a display it did not ask for.

## Why it matters beyond emulators

This is not an arcade-specific fix. **Any** Qt or GTK application started on the
headless display paid the 120s tax, and a "hang" blamed on RetroArch earlier in
the week is very likely the same cause.

## How to spot it again

`systemctl --user status xdg-desktop-portal-gtk.service`. If it is `failed` with
`cannot open display`, the user manager has lost `DISPLAY`/`XAUTHORITY` — most
likely because the X session moved, the auth file path changed, or the drop-in
was lost in a rebuild.
