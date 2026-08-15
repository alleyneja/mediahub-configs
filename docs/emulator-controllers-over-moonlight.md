# Emulator controllers over Moonlight — how the virtual pad actually presents

## 2026-08-15 — first working controller at the projector, and what broke on the way

The Xbox Series pad pairs by Bluetooth **to the projector** (Android, native BT),
not to mediahub. Moonlight forwards standard gamepad reports; Sunshine
materialises a virtual pad on the host through `/dev/uinput`. mediahub needs no
Bluetooth hardware for this and none is involved.

## The virtual pad's identity — three different names for one device

This is the thing that cost the most time. The same device is called three
different things depending on who is asking:

| Layer | Name it reports |
|---|---|
| evdev / `/proc/bus/input/devices` | `Sunshine X-Box One (virtual) pad` |
| SDL (Dolphin, PCSX2) | `Microsoft Xbox One` / `Xbox One Controller` |
| Sunshine log | `Gamepad 0 will be Xbox One controller` |

**Bind SDL-based emulators to the SDL name, never the evdev name.** SDL matches
the device against its bundled controller database (721 mappings) and presents a
normalised name; the raw evdev string never reaches the emulator.

## Device index: `SDL-0` is the pad, and `js0` is not

PCSX2 binds by index, and the log line that matters is:

```
SDLInputSource: Gamepad 1 inserted
SDLInputSource: Opened gamepad 1 (instance id 1, player id 0): Microsoft Xbox One
SDLInputSource: Gamepad 0 has 6 axes and 11 buttons
```

The `1` is the **SDL joystick index**; the identifier PCSX2 uses in binding
strings is the **player id**, which is `0`. So bindings read `SDL-0/FaceSouth`
even though the pad enumerates at joystick index 1.

Index 0 is taken by `/dev/input/js0`, which is **Sunshine's virtual mouse**, not a
controller. Do not "fix" `SDL-0` to `SDL-1` on the strength of the inserted-index
line — that breaks working bindings. (Done and reverted on 2026-08-15.)

## Hotplug works — launch order does not matter

A theory that emulators missed the pad because Sunshine creates it at the same
moment it launches the app is **wrong**. Verified by injecting a synthetic pad
159 seconds after PCSX2 had started:

```
[  159.7984] SDLInputSource: Gamepad 1 inserted
[  159.7985] Opened gamepad 1 (instance id 1, player id 0): Xbox One Controller
```

PCSX2 picks up a pad that appears long after launch. No pre-launch delay or
Sunshine app-entry change is needed.

## Testing input without a human at the pad

`/dev/uinput` is writable by `jay`, so a synthetic Xbox-shaped pad can be created
and driven from a script — useful when the projector is in another room. Present
Microsoft VID/PID (`045e:02ea`) so SDL's database recognises it and assigns the
same abstract button names real hardware gets.

A working implementation lives outside this repo in the session scratchpad; the
essentials are `UI_SET_EVBIT`/`UI_SET_KEYBIT`/`UI_SET_ABSBIT` ioctls, a
`uinput_user_dev` struct write, `UI_DEV_CREATE`, then 24-byte `input_event`
writes. Confirm the result with a framebuffer capture rather than assuming.

## Per-emulator status

| Emulator | Input stack | Status |
|---|---|---|
| RetroArch | `udev` (`input_joypad_driver = "udev"`) | Works with zero config — autoconfig matches the Xbox profile at runtime |
| PCSX2 | SDL, binds by index | Works. `SDL-0/*` bindings, verified driving a game |
| Dolphin | SDL, binds by name | Device line set to `SDL/0/Microsoft Xbox One` — **not yet verified** |

**RetroArch was the only one that worked out of the box because it uses `udev`,
not SDL.** That is the dividing line, not emulator quality.

## Two behaviours that look like bugs and are not

- **PCSX2's desktop game list cannot be navigated with a pad.** Controller
  bindings drive the *emulated PS2 controller*, which only exists once a game is
  booted. Use Big Picture mode (`-bigpicture`) for a pad-navigable launcher.
- **Sunshine ends the stream when the app it launched exits.** Killing an
  emulator from a shell therefore disconnects the client and destroys the
  virtual pad. Expect the pad to vanish mid-debugging if you do this.

## Dolphin — pre-existing mapping bug found

`GCPadNew.ini` had the GameCube analog triggers bound to keyboard keys:

```ini
Triggers/L = `Q`      # was
Triggers/R = `W`      # was
Triggers/L = `Trigger L`   # now
Triggers/R = `Trigger R`   # now
```

This was broken at the desk too, since the 2026-08-11 mapping pass. Wiimote slots
remain unconfigured (`XInput2/0/Virtual core pointer`) — irrelevant over Moonlight,
which never forwards motion, IR or MotionPlus.

## Configs assume streaming

Both `GCPadNew.ini` and `PCSX2.ini` now target the virtual pad. Desk play with a
physically-attached controller needs them switched back; originals are saved
alongside as `*.bak-desk-20260815`.
