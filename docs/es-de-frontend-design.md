# ES-DE frontend — design

**Status:** approved 2026-08-17, not yet implemented.
**Supersedes:** the "Frontend / launcher" open item in `~/mediahub-arcade.md`.

## Problem

Three emulators are installed, mapped and verified, but there is no pad-navigable
way to pick a game:

- **Dolphin has no front door at all.** Its game list is mouse-only and it has no
  Big Picture equivalent. Nothing about Dolphin is broken — it simply cannot be
  driven from the couch.
- **PCSX2 has Big Picture**, which works and was signed off 2026-08-15.
- **RetroArch** has its own pad-navigable UI.

So the experience is inconsistent, and adding a Sunshine tile per emulator does
not scale. One launcher in front of all three gives a single Sunshine entry, one
artwork pass, and the same loop for every system.

**This was blocked until 2026-08-17** on the worry that a frontend needs its own
GPU context and would hit the same present-queue failure as PCSX2 Big Picture.
That risk is retired — see `pcsx2-bigpicture-vulkan-present-queue.md`.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Existing Sunshine tiles | **Keep all three, add ES-DE as a fourth** | Nothing working gets disturbed. If ES-DE misbehaves at the projector there are three proven ways in. Collapse to a single tile later as its own trivial change. |
| Exit path | **Always back to ES-DE** | One loop for every system. Costs a per-emulator *quit* binding. |
| Artwork / metadata | **ES-DE's built-in scraper against ScreenScraper** | Best coverage for GC/Wii/PS2/N64/GBA incl. logos and marquees, which the themes are built around. |
| GameCube directory | **Do not rename. Override on the ES-DE side.** | See below — the name is load-bearing for RomM. |
| PS3 / Switch | **Hidden**, via a pad-reachable toggle | A launcher full of things that fail to start is worse than a shorter list. |

## Architecture

```
Moonlight (projector, Xbox Series pad over BT)
   │
   ▼
Sunshine tile "Arcade"  ──launches──►  ES-DE (AppImage, not sandboxed)
                                          │
                                          │ launches with ROM as argument
                                          ▼
                        flatpak run org.libretro.RetroArch   (n64, gba)
                        flatpak run org.DolphinEmu.dolphin-emu (ngc, wii)
                        flatpak run net.pcsx2.PCSX2          (ps2)
                                          │
                                          │ pad-reachable QUIT binding
                                          ▼
                                       back to ES-DE
```

Emulators are **not modified**. Every binding verified on 2026-08-15 continues to
apply, because ES-DE only changes *who launches them*, not how they run.

## Install

- ES-DE ships as an **AppImage** (it is not on Flathub). Install to
  `~/Applications/`.
- **ES-DE is not sandboxed**, unlike the emulator Flatpaks, so it needs no
  `flatpak override` to reach `/mnt/internal/arcade`. This is a genuine
  simplification versus every emulator added so far.
- Archive the AppImage to `/mnt/internal/arcade/emulators/` and record version +
  source URL in the README there, per the Phase 0 custody habit. Frontends are
  less legally fraught than emulators, but the habit costs nothing.

## Library wiring

ROM root: `/mnt/internal/arcade/roms`. ES-DE config, gamelists and
`downloaded_media` under `~/ES-DE/`.

**Both paths are mmap-safe.** `/home/jay` is on `/dev/nvme0n1p2` and
`/mnt/internal` is `/dev/sda1` ext4 — neither is the mergerfs pool, so the
`cache.files=off` / ENODEV trap that broke qBittorrent and Calibre does not
apply. **Do not relocate either onto `/mnt/media`.**

### The GameCube slug conflict — do NOT rename the folder

RomM's live database:

```
fs_slug   slug   name
ngc       ngc    Nintendo GameCube
```

`ngc` is RomM's **filesystem slug** and is how RomM identified that directory as
GameCube. RomM's config directory is **empty** — there is no `config.yml`
providing a name mapping, so it depends entirely on the folder name. Renaming
`ngc` → `gc` orphans the catalogued GameCube entries.

ES-DE expects `gc`. Neither tool is wrong; they follow different conventions
(RomM uses IGDB slugs) and one side must absorb a mapping.

**Resolution: absorb it in ES-DE**, which is not yet installed, rather than
creating new RomM config to tolerate a rename that buys nothing. Add a custom
system entry pointing GameCube at `ngc`:

```xml
<!-- ~/ES-DE/custom_systems/es_systems.xml -->
<system>
  <name>gc</name>
  <fullname>Nintendo GameCube</fullname>
  <path>%ROMPATH%/ngc</path>
  ...
</system>
```

No files move and RomM never notices. **Confirm the exact custom-systems path and
schema against the installed ES-DE version** rather than trusting this snippet
verbatim.

**Equally, do not symlink `gc` → `ngc`.** RomM scans that same directory and
would catalogue both names, reproducing the duplicate-entry problem cleaned up on
2026-08-11.

## Launch and exit

ES-DE invokes the existing Flatpaks with the ROM as an argument. PS2 keeps its
BIOS tower animation — that is PCSX2's `EnableFastBoot = false`, a config setting,
not a launch flag, so it is unaffected.

**The exit binding is the real work.** "Back to ES-DE every time" requires each
emulator to *quit*, not merely close the game:

- **PCSX2** — has `OpenPauseMenu = SDL-0/Guide` already. Confirm the pause menu's
  exit action terminates the process rather than returning to its game list.
- **Dolphin** — needs a pad-reachable quit binding. None exists today.
- **RetroArch** — has an exit hotkey concept; needs binding to the pad.

**Controller identity trap:** ES-DE uses **SDL**, so it will report the pad as
`Microsoft Xbox One`, not the evdev name `Sunshine X-Box One (virtual) pad`. This
is the same trap that cost time on both Dolphin and PCSX2. RetroArch differs
because it uses `udev` — that is the dividing line.

## Sunshine integration

New entry in `sunshine/apps.json`:

```json
{ "name": "Arcade (ES-DE)", "cmd": "/home/jay/Applications/ES-DE.AppImage" }
```

Added **alongside** the existing Desktop / RetroArch / Dolphin / PCSX2 entries.
Sunshine ends the session when its launched app exits, so quitting ES-DE cleanly
ends the stream — the desired behaviour.

**Unrelated small follow-up, do it in the same pass:** the PCSX2 entry is still on
the plain launch. Re-add `-bigpicture -fullscreen` now that it works mid-stream.

## Scraping

**Prerequisite: a free ScreenScraper account** (screenscraper.fr). Scraping is
heavily rate-limited without one. Jay must create it; it cannot be automated.

First full scrape is slow and should run unattended. Media lands in ES-DE's own
`downloaded_media` tree and is entirely separate from RomM's database — the two
catalogues coexist without interacting.

## Hiding PS3 and Switch

Both are visible-but-unplayable on this hardware: Switch is a hard 2GB VRAM wall,
PS3 is deferred to Phase 4 and RPCS3 is not installed. `switch/` is empty at this
path anyway (the 47G lives on the pool, bind-mounted into RomM only).

Use the per-game **`hidden` metadata flag**, because it pairs with a **"Show
hidden games" toggle in ES-DE's UI settings** — reachable from the couch with the
pad, and reversible in both directions.

Command-line equivalent: `<hidden>true</hidden>` per entry in
`~/ES-DE/gamelists/ps3/gamelist.xml`.

**Confirm the exact menu label at install time.** **Hiding is cosmetic** —
unhiding makes the entries appear but they still will not run.

## Verification plan

Nothing here is assumed working until shown. In order:

1. **ES-DE renders mid-stream on the OpenGL path.** The 2026-08-17 present-queue
   proof was on **Vulkan** (PCSX2); ES-DE is expected to be SDL + **OpenGL**. The
   fix was at the connector/presentation level and should cover both, but this is
   the single largest untested assumption in this design. Test the direction that
   used to fail: connect Moonlight to **Desktop** first, *then* launch ES-DE.
2. **Pad navigates ES-DE** — verify against the SDL name.
3. **Each of the five systems launches** a game from ES-DE: n64, gba, ngc, wii,
   ps2.
4. **Each returns to ES-DE on quit.**
5. **PS3/Switch hidden**, and the unhide toggle works.
6. **Hardware GL is genuinely live** — newly available and never measured.
   RetroArch was pinned to `video_driver = "gl"` with no hardware GL under
   `xf86-video-dummy`, and Dolphin's slow OpenGL boots were software rendering.
   Compare Dolphin boot times against the 08-15 baseline.

**Useful during testing:** `/dev/uinput` is writable, so a synthetic
Xbox-VID/PID pad can be driven from a script and verified by framebuffer capture
— no walk to the projector needed. Time injected presses **to the game, not the
launch**.

## Rollback

Delete the `Arcade` tile from `apps.json`. The three existing tiles are untouched
and remain fully functional, which is the entire reason for the additive design.
Removing `~/ES-DE/` and the AppImage reverts the rest. **No change is made to any
emulator, any ROM, or RomM.**

## Traps carried in from earlier sessions

- **A black screen on the Desktop tile is normal** — bare openbox, nothing to
  draw. `xsetroot -solid "#B22222"` on `:1` is the fast capture check.
- **Quitting the Sunshine-launched app ends the stream.** Any mid-stream test
  needs the client reconnected.
- **One emulator instance at a time.** Two stack identically-positioned windows
  and hide modals.
- **Avoid portal file pickers on this host** — they open behind fullscreen
  windows and block the app. Recover with
  `systemctl --user restart xdg-desktop-portal-gtk.service`.
- **Powering off the projector does not end a Moonlight session.**

## Out of scope

- Collapsing to a single Sunshine tile (deliberate follow-up once ES-DE is
  trusted).
- Installing RPCS3 or enabling PS3/Switch — Phase 4 hardware.
- Theme customisation beyond whatever ES-DE ships with.
- Any change to RomM.
