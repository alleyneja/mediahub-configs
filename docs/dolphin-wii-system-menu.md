# Dolphin: installing the Wii System Menu and Mii Channel

**Status: done and verified.** System Menu **4.3U** and the **Mii Channel** are installed in the
emulated NAND and both boot. Installed 2026-08-25, boot re-verified 2026-08-30.

## The problem

Pressing HOME in a Wii game and choosing **Wii Menu** blanked the screen. Not a crash — Dolphin kept
running and the Wiimote stayed linked. The cause was that `title/00000001/00000002/` held only
`data/` and **no `content/`**: Dolphin creates that skeleton for settings whether or not the System
Menu exists, so selecting "Wii Menu" booted a title that wasn't there. The same gap meant there was
no Mii database and no way to create Miis — Wii Sports was falling back to built-in Guest Miis.

## The fix — no WAD hunting, no console dump

Nintendo's NUS CDN still serves Wii system titles. Verified before touching anything:

```
curl -s http://nus.cdn.shop.wii.com/ccs/download/0000000100000002/tmd   # 2528 B, RSA-2048 sig
```

The TMD came back signed by `Root-CA00000001-CP00000004` — Nintendo's own chain. Dolphin also ships
`fakenus.dolphin-emu.org` as a fallback mirror, which is up.

**Back up the NAND first**, then use Dolphin's built-in installer:

```
tar -czf /mnt/internal/arcade/backups/wii-nand-pre-sysmenu-$(date +%Y%m%d-%H%M%S).tar.gz \
    -C /mnt/internal/arcade/saves/dolphin Wii
```

Then **Tools → Perform Online System Update → United States** (match the library's region — the
wrong region installs the wrong System Menu). Launch Dolphin **standalone** for this: ES-DE invokes
it as `-b -e <rom>`, which boots straight into a game and bypasses every menu.

Result: NAND **18M/43 files → 121M/348 files**, 49 IOSes, and `Tools → Load Wii System Menu` went
from greyed out to **"Load Wii System Menu 4.3U"**. The **Mii Channel came down automatically**
(`title/00010002/48414341`, "HACA"), along with Photo, Shopping, Weather, News and Photo 1.1, plus
EULA and Region Select under `00010008`.

**Saves are not touched by the update** — `RPSports.dat` and `Sports2.dat` came through byte-identical.

## Booting a NAND title directly

`-n` takes a 16-character title ID and needs no disc. Both verified by screenshot on `:1`:

```
dolphin-emu -b -C Dolphin.Display.Fullscreen=True -n 0000000100000002   # System Menu 4.3U
dolphin-emu -b -C Dolphin.Display.Fullscreen=True -n 0001000248414341   # Mii Channel, direct
```

The System Menu comes up on the real Health & Safety screen; the Mii Channel goes **straight to
"Welcome to the Mii Channel!"**, skipping the menu and the health screen.

**First frame takes ~45 seconds.** At t+30s the capture is still 96.8% black with 107 distinct
colours — that is the normal boot, *not* the old missing-title failure. At t+50s it is 13,835
colours dominated by `#ffffff` and `#ffdfbd`, the Wii Menu palette. Anyone verifying this headlessly
must wait past 45s before calling it a black screen. See `headless-visual-verification.md`.

## Getting back to ES-DE

`Hotkeys.ini` binds `General/Stop = Guide` on `SDL/0/Xbox One S Controller`, and `Dolphin.ini` sets
`ConfirmStop = False`. Because ES-DE launches Dolphin with `-b`, stopping emulation **exits Dolphin**
and returns to ES-DE. So the **Xbox Guide button** is the way out, from a game, the System Menu or
the Mii Channel.

**The Xbox pad is the only exit.** `WiimoteNew.ini` has Wiimote1/2 on `Source = 2` (real Wii Remote),
and real Wiimotes are passed through to the emulated Wii — Dolphin cannot read them as a hotkey
device. Keep the pad within reach or you are stuck in the Mii Channel.

Note `WiimoteNew.ini` also has `Buttons/Home = Guide` on Wiimote1 — the same button as Stop. Inert
today because `Source = 2` bypasses the emulated bindings, but on `Source = 1` one press would fire
HOME and Stop together.

## Mii database

Launching the Mii Channel creates `shared2/menu/FaceLib/RFL_DB.dat` (779,968 bytes, preallocated) on
first run. It exists and is empty as of 2026-08-30. Miis are made with the **Wiimote pointer**, so
this needs a person at the projector. Wii Sports reads this database directly, so Miis created here
replace the Guest Miis in-game.

## Paths

Dolphin is a **flatpak**. User dir is `~/.var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu/`, and
`Wii/` inside it is a **symlink to `/mnt/internal/arcade/saves/dolphin/Wii`** — the real NAND.
Config lives under `~/.var/app/org.DolphinEmu.dolphin-emu/config/dolphin-emu/`.
