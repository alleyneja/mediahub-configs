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
| Dolphin | SDL, binds by name | Works. `SDL/0/Microsoft Xbox One`, **verified** driving Wind Waker |

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

## PCSX2 ships zero controller hotkeys — bind `OpenPauseMenu` yourself

Same class of gotcha as udev-vs-SDL. Out of the box **every** entry in `[Hotkeys]`
is keyboard-only:

```ini
OpenPauseMenu = Keyboard/Escape
TogglePause   = Keyboard/Space
```

RetroArch ships a default guide-button menu toggle; PCSX2 ships nothing for
controllers. At a projector with no keyboard that means no way out of a running
game — which looks like "you have to kill it from a terminal", but is just a
missing binding.

```ini
OpenPauseMenu = SDL-0/Guide
```

`Guide` is a valid PCSX2 SDL button name — **verified** by injecting a synthetic
press mid-game and confirming the pause menu opened. The menu's **Close Game**
item returns to Big Picture.

Caveat: some Moonlight clients capture the guide button for their own overlay
before forwarding it. If it never reaches PCSX2, bind `SDL-0/Back` or a chord.

## The iconic PS2 boot animation

`EnableFastBoot = true` (the default) skips the BIOS entirely and jumps to the
game's ELF. Set it `false` for the full Sony logo and tower animation before
each game. Costs ~10-15s per launch. The tower count reflects saves on the
memory card. `-bios` (or Big Picture's **Start Game** with no disc) boots to the
PS2 System Menu instead.

## Audio — Sunshine was never the problem

An early session appeared to have no audio and `sunshine-sink` looked "inactive".
Both readings were wrong:

- **`sunshine-sink` is not a systemd unit.** It is a PipeWire filter-chain node
  (`pipewire -c filter-chain.conf`). Querying it with `systemctl` is meaningless.
- **The audio stack runs as `gdm`, not `jay`** — so `pactl` as jay finds nothing.
  If gdm's session restarts, the sink goes with it.

Sunshine's own log shows the pipeline healthy on every session including the
silent one:

```
Setting default sink to: [sink-sunshine-stereo]
Found default monitor by name: sunshine-sink.monitor
Opus initialized: 48 kHz, 2 channels, 96 kbps (total), LOWDELAY
```

Sunshine also flips the default sink to `sink-sunshine-stereo` for the duration
of a session and back to `sunshine-sink` afterwards. **Check the application's
audio device before suspecting Sunshine.** Cause of the original silence
(RetroArch) remains unconfirmed.

## PCSX2 — settled 2026-08-15

Jay's words: "this is exactly what I want the PS2 emulator to be like." Working
loop, fully controller-driven, no mouse or keyboard anywhere:

**Big Picture → pick game → PS2 BIOS animation → play → Guide → Close Game → Big Picture**

Config: `SDL-0/*` pad bindings, `OpenPauseMenu = SDL-0/Guide`,
`EnableFastBoot = false`, renderer Vulkan on the P400.
Backups: `PCSX2.ini.bak-desk-20260815`, `PCSX2.ini.bak-prehotkey-20260815`.

Still gated by the Big Picture launch bug — see
`pcsx2-bigpicture-vulkan-present-queue.md`.

## Dolphin — verified 2026-08-15

`GCPadNew.ini` now reads `Device = SDL/0/Microsoft Xbox One`, **confirmed working**
by launching Wind Waker headless and injecting a synthetic START. The game moved
off its title screen into the opening narration.

```bash
flatpak run org.DolphinEmu.dolphin-emu -b -e "/mnt/internal/arcade/roms/ngc/<game>.ciso"
```

Two things learned in the process:

- **Dolphin hotplugs pads**, same as PCSX2. A device created after launch is
  picked up without a restart.
- **Time the injection to the game, not the launch.** The first attempt pressed
  START 40s in, while Wind Waker was still booting, and nothing happened — which
  looks *identical* to a broken binding. Dolphin runs on **OpenGL**, which is
  software-rendered under `xf86-video-dummy`, so boots are slow. Screenshot
  first, confirm the game is at an interactive screen, then inject.

Still untested: the corrected `Trigger L`/`Trigger R` bindings. Wiimote slots
remain unconfigured and are permanently irrelevant over Moonlight.

**Dolphin has no Big Picture equivalent.** Its game list is mouse-only, so there
is no pad-navigable way to pick a game. That is a launcher problem, not a Dolphin
config problem — see the frontend note below.

## Two traps that only appeared after the portal was fixed

**Portal file dialogs bury themselves behind fullscreen windows.** PCSX2's
per-game "Set Cover Image" opens a GTK file chooser via
`xdg-desktop-portal-gtk`. On a bare openbox session it opens *behind* a
fullscreen game and blocks the app — looks like a hang, is not one. Note this
failure mode **did not exist before the portal was fixed**, because portal calls
simply timed out and no dialog ever appeared.

Recovery without killing the emulator — the dialog belongs to the portal
process, not the app:

```bash
systemctl --user restart xdg-desktop-portal-gtk.service
```

Avoid file-choosers entirely on this host. For cover art, drop files into
`covers/` named by serial (`SLUS-21678.jpg`); PCSX2's bulk **Cover Downloader**
is also a desktop-UI window and stacks the same way. Verified working source:
`https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/${serial}.jpg`
(the `3d/` path 404s).

**One emulator instance at a time.** Launching PCSX2 from the Moonlight app list
while another instance is already running gives two sets of windows at identical
geometry, and a modal on one hides under the other's fullscreen window. Check
before launching:

```bash
ps -eo pid,cmd | grep "[p]csx2-qt"
grep -q "Sunshine X-Box One (virtual) pad" /proc/bus/input/devices && echo "client connected"
```

**Powering off the projector does not end a Moonlight session.** The client keeps
streaming to a dark display and Sunshine cannot tell. Quit Moonlight properly, or
check the pad node above.

## Frontend / launcher — evaluated 2026-08-15, deferred

One pad-navigable launcher in front of all three emulators would give a single
Sunshine entry, one artwork scrape for every system, and — most importantly — a
way to pick GameCube/Wii games with a pad, which Dolphin cannot do alone.

- **Ampchor** (ampchor.com) — **not suitable.** It is a media server/player
  (movies, TV, music, books) whose retro games are played *inside the app*, i.e.
  embedded emulation with roughly the EmulatorJS ceiling. It will not drive
  GameCube, Wii or PS2, and duplicates Plex/Calibre/Audiobookshelf.
- **Playnite** — Windows-only .NET/WPF. Not viable natively.
- **ES-DE** — the purpose-built option. Not on Flathub; AppImage install.
- **Pegasus** — on Flathub, alpha. **Lutris** — on Flathub, PC-game oriented.

**Deferred until the mini-DP plug is fitted.** A frontend needs its own GPU
context and may hit the same present-queue failure as PCSX2's Big Picture. Build
it only once that is resolved, or two unknowns get debugged at once.

---

## 2026-08-17 — Dolphin's "verified" binding was a FALSE POSITIVE

**Dolphin had never actually been controllable over Moonlight.** The 08-15 entry
recording `SDL/0/Microsoft Xbox One` as verified was wrong, and the reason is the
verification method, not the binding syntax.

**What happened:** that binding was confirmed by driving Wind Waker with a
**synthetic `/dev/uinput` pad** built with Xbox VID/PID. SDL derives a device's
friendly name from VID/PID, so the synthetic pad and Sunshine's real virtual pad
got **different SDL names**. The test passed against a device that is never used
in real play.

**Sunshine's actual virtual pad**, as reported by SDL:

```
"Xbox One S Controller"  GUID 03008d205e040000ea02000008040000
```

That GUID is VID `045e` / PID `02ea` — Microsoft Xbox One S. The evdev name stays
`Sunshine X-Box One (virtual) pad`; only the SDL name differs.

**Fix:** `GCPadNew.ini` → `[GCPad1]` → `Device = SDL/0/Xbox One S Controller`.
Confirmed by Jay with Super Mario Sunshine: pad responsive, audio correct.

**Why only Dolphin was affected:** it is the only emulator here that binds by
device *name*. PCSX2 binds by **player id** (`SDL-0`) and RetroArch uses **udev**
with the evdev name — both are immune to SDL renaming, which is why those two
genuinely worked at the projector while Dolphin's success was illusory.

### Lesson: synthetic input cannot verify a name-bound device

Injecting from `/dev/uinput` still works for *timing* and *button-mapping* tests,
but it **cannot validate a binding that keys on the device name** — the synthetic
device is a different device. For anything name-bound, test with the real pad
through a real Moonlight session. This compounds with the 08-17 finding that
framebuffer capture no longer works on the P400, so there is now no way to
validate Dolphin input without a human at the projector.

### Still open: Wii input is not mapped at all

`WiimoteNew.ini` has every slot on `XInput2/0/Virtual core pointer` — the mouse.
Wii games launch but do not respond to the pad. This is the long-standing
"Wii Remote + MotionPlus configured separately" Phase 3 item, never done. Note
Wii Sports Resort cannot be solved this way regardless (needs real MotionPlus);
titles with Classic Controller support are the ones worth mapping.
