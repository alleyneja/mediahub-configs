# ES-DE Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give mediahub-production a single pad-navigable launcher (ES-DE) in front of RetroArch, Dolphin and PCSX2, reachable as one Sunshine tile from the projector.

**Architecture:** ES-DE runs as an unsandboxed AppImage on the headless `:1` display, launches the existing emulator Flatpaks with the ROM as an argument, and is added as a *fourth* Sunshine tile alongside the three existing ones. No emulator, ROM, or RomM configuration is modified.

**Tech Stack:** ES-DE 3.4.1 (AppImage), Flatpak emulators (RetroArch 1.22.2, Dolphin 2606, PCSX2 v2.6.3), Sunshine + NvFBC on Xorg `:1`, ScreenScraper for metadata.

**Spec:** `mediahub-configs/docs/es-de-frontend-design.md`

## Global Constraints

- **Host:** mediahub-production. Everything runs as `jay`. Display is `:1` — every GUI command needs `DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth`.
- **ROM root:** `/mnt/internal/arcade/roms` — **never** `/mnt/media` (mergerfs `cache.files=off` blocks mmap).
- **ES-DE app data:** `/home/jay/ES-DE` (NVMe root, mmap-safe).
- **Do NOT rename `roms/ngc`.** It is RomM's filesystem slug. ES-DE absorbs the mapping.
- **Do NOT symlink `gc` → `ngc`.** RomM scans the same directory and would double-catalogue.
- **`mediahub-configs` is a PUBLIC repo.** ScreenScraper credentials must never be committed.
- **One emulator instance at a time** — two stack identically-positioned windows and hide modals.
- **Sunshine ends the stream when its launched app exits.** Any mid-stream test needs the client reconnected to the **Desktop** tile.
- **Controller identity:** SDL-based apps see `Microsoft Xbox One`; RetroArch uses `udev` and sees `Sunshine X-Box One (virtual) pad`.
- **Commit every config change to `mediahub-configs`** before the session ends.

---

### Task 1: Finish library hygiene

Three archives are still on disk because their extractions are not yet boot-confirmed. **If they are still present when ES-DE scrapes, every one of those games is catalogued twice.**

**Files:**
- Delete (after confirmation): `/mnt/internal/arcade/roms/ps2/Jak and Daxter Complete Trilogy (USA).7z`
- Delete (after confirmation): `/mnt/internal/arcade/roms/wii/Wii Play (USA, Canada) (Rev 1).7z`
- Delete (after confirmation): `/mnt/internal/arcade/roms/wii/Wii Sports (USA) (Rev 1).7z`

- [ ] **Step 1: Boot each extracted game once**

One instance at a time. Confirm each reaches its title screen, then quit before starting the next.

```bash
export DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth
flatpak run net.pcsx2.PCSX2 "/mnt/internal/arcade/roms/ps2/Jak and Daxter Complete Trilogy (USA).iso"
# quit, then:
flatpak run org.DolphinEmu.dolphin-emu "/mnt/internal/arcade/roms/wii/Wii Play (USA, Canada) (Rev 1).wbfs"
# quit, then:
flatpak run org.DolphinEmu.dolphin-emu "/mnt/internal/arcade/roms/wii/Wii Sports (USA) (Rev 1).wbfs"
```

Expected: each reaches a title/menu screen. Jak and Daxter is a single 4.5 GB ISO containing the trilogy — expect a game-select menu, not three separate discs.

- [ ] **Step 2: Delete the three archives**

Only after all three booted.

```bash
cd /mnt/internal/arcade/roms
rm -v "ps2/Jak and Daxter Complete Trilogy (USA).7z" \
      "wii/Wii Play (USA, Canada) (Rev 1).7z" \
      "wii/Wii Sports (USA) (Rev 1).7z"
```

- [ ] **Step 3: Verify one file per game**

```bash
find /mnt/internal/arcade/roms -maxdepth 2 -type f -name '*.7z' ! -path '*/ps3/*'
```

Expected: **no output**. Any `.7z` outside `ps3/` means a duplicate entry is still waiting to happen.

- [ ] **Step 4: Trigger a RomM rescan**

Still-open item from Phase 1, and now genuinely needed — five files changed. RomM UI → Scan. Confirm GameCube still reads as `Nintendo GameCube` and no duplicates appear.

---

### Task 2: Install ES-DE

**Files:**
- Create: `/home/jay/Applications/ES-DE_x64.AppImage`
- Create: `/mnt/internal/arcade/emulators/ES-DE_x64-3.4.1.AppImage` (archive copy)
- Modify: `/mnt/internal/arcade/emulators/README` (record version + source)

- [ ] **Step 1: Download the AppImage**

ES-DE 3.4.1, released 2026-04-10. Not on Flathub — AppImage is the Linux route.

```bash
mkdir -p /home/jay/Applications
curl -L -o /home/jay/Applications/ES-DE_x64.AppImage \
  "https://gitlab.com/es-de/emulationstation-de/-/package_files/288156961/download"
chmod +x /home/jay/Applications/ES-DE_x64.AppImage
```

- [ ] **Step 2: Verify it is a real AppImage and runs**

```bash
file /home/jay/Applications/ES-DE_x64.AppImage
/home/jay/Applications/ES-DE_x64.AppImage --version
```

Expected: `ELF 64-bit LSB executable`, and a version string reporting 3.4.1. If `--version` is unsupported, `--help` is an acceptable substitute.

- [ ] **Step 3: Archive per the Phase 0 custody habit**

```bash
cp /home/jay/Applications/ES-DE_x64.AppImage \
   /mnt/internal/arcade/emulators/ES-DE_x64-3.4.1.AppImage
```

Append to `/mnt/internal/arcade/emulators/README`: version `3.4.1`, date `2026-08-17`, source `https://es-de.org/` (GitLab package_files/288156961).

- [ ] **Step 4: Commit**

```bash
cd ~/mediahub-configs
git add -A && git commit -m "docs: record ES-DE 3.4.1 AppImage install"
git push origin main
```

---

### Task 3: ⚠️ RISK GATE — prove ES-DE renders mid-stream

**This is the single largest untested assumption in the design and it is deliberately placed before any configuration work.** The 2026-08-17 present-queue proof was on the **Vulkan** path (PCSX2). ES-DE renders through SDL + **OpenGL**. If OpenGL presentation fails mid-stream, the whole design needs rethinking — better to learn that now than after a scrape.

- [ ] **Step 1: Establish a live stream**

Jay connects Moonlight to the **Desktop** tile and leaves it up. Confirm from the host:

```bash
nvidia-smi --query-gpu=encoder.stats.sessionCount,encoder.stats.averageFps --format=csv,noheader
```

Expected: `1, 30` (or similar non-zero fps). `0, 0` means no client — the test is invalid, do not proceed.

- [ ] **Step 2: Confirm capture is live**

```bash
DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth xsetroot -solid "#B22222"
```

Expected: projector turns red. This is the fast capture check.

- [ ] **Step 3: Launch ES-DE against the live session**

```bash
export DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth
/home/jay/Applications/ES-DE_x64.AppImage 2>&1 | tee /tmp/es-de-firstrun.log
```

- [ ] **Step 4: Verify it rendered, do not assume**

```bash
grep -iE "GL_|OpenGL|renderer|error|failed|present" /tmp/es-de-firstrun.log | head -20
DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth xwininfo -root -children | grep -i "es-de\|emulationstation"
```

Expected: a mapped ES-DE window, no fatal GL errors, **and Jay confirms he can see it on the projector.** A render test does not prove capture — the 2026-08-14 lesson.

- [ ] **Step 5: STOP if this fails**

If ES-DE cannot create a GL context mid-stream, do not continue. Record the exact error and revisit the spec — the fallback is moving Sunshine's encode to the Intel iGPU, which the spec currently rules out as unnecessary.

---

### Task 4: Point ES-DE at the library and fix the GameCube slug

**Files:**
- Modify: `/home/jay/ES-DE/settings/es_settings.xml` (ROM directory)
- Create: `/home/jay/ES-DE/custom_systems/es_systems.xml`

**Interfaces:**
- Consumes: a working ES-DE install from Task 2, proven renderable from Task 3.
- Produces: five detected systems — `gba`, `n64`, `gc`, `ps2`, `wii` — with `gc` reading from the `ngc` directory.

- [ ] **Step 1: Set the ROM directory**

On first run ES-DE creates `~/ES-DE/` and asks for a ROM directory. Set it to `/mnt/internal/arcade/roms`. Verify:

```bash
grep -i "ROMDirectory" /home/jay/ES-DE/settings/es_settings.xml
```

Expected: value `/mnt/internal/arcade/roms`.

- [ ] **Step 2: Confirm GameCube is missing — the failure this task fixes**

Restart ES-DE and look at the system list. Expected: `gba`, `n64`, `ps2`, `wii` appear; **GameCube does not**, because ES-DE looks for `gc` and the directory is `ngc`. Confirming the absence first is what makes the fix verifiable.

- [ ] **Step 3: Write the custom system entry**

```bash
mkdir -p /home/jay/ES-DE/custom_systems
cat > /home/jay/ES-DE/custom_systems/es_systems.xml <<'XML'
<?xml version="1.0"?>
<systemList>
    <system>
        <name>gc</name>
        <fullname>Nintendo GameCube</fullname>
        <path>%ROMPATH%/ngc</path>
        <extension>.ciso .CISO .iso .ISO .gcm .GCM .gcz .GCZ .rvz .RVZ .7z .7Z .zip .ZIP</extension>
        <command label="Dolphin">flatpak run --command=dolphin-emu org.DolphinEmu.dolphin-emu -b -e %ROM%</command>
        <platform>gc</platform>
        <theme>gc</theme>
    </system>
</systemList>
XML
```

Note `<path>` is `%ROMPATH%/ngc` while `<name>` stays `gc` — that mismatch is the entire point. `-b -e` makes Dolphin boot the game directly and exit on quit.

- [ ] **Step 4: Verify GameCube now appears with both games**

Restart ES-DE. Expected: a **Nintendo GameCube** system containing Wind Waker and Super Mario Sunshine.

- [ ] **Step 5: Verify RomM is undisturbed**

```bash
PW=$(docker inspect romm-db --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ROOT_PASSWORD | head -1 | cut -d= -f2-)
docker exec romm-db mariadb -uroot -p"$PW" -N -e "select fs_slug, name from romm.platforms;"
```

Expected: `ngc  Nintendo GameCube` still present. Nothing about RomM should have changed — this step exists to prove it.

- [ ] **Step 6: Commit**

```bash
mkdir -p ~/mediahub-configs/es-de
cp /home/jay/ES-DE/custom_systems/es_systems.xml ~/mediahub-configs/es-de/custom_es_systems.xml
cd ~/mediahub-configs
git add -A && git commit -m "es-de: custom GameCube system mapping gc -> roms/ngc"
git push origin main
```

---

### Task 5: Verify every system launches a game

**Interfaces:**
- Consumes: five configured systems from Task 4.
- Produces: a confirmed-working launch path per system, needed before the exit bindings in Task 6 are meaningful.

- [ ] **Step 1: Launch one game per system from inside ES-DE**

| System | Game | Emulator |
|---|---|---|
| gba | Pokemon Mystery Dungeon | RetroArch (mGBA core) |
| n64 | Super Smash Bros. | RetroArch (Mupen64Plus-Next) |
| gc | Wind Waker | Dolphin |
| wii | Wii Sports Resort | Dolphin |
| ps2 | Budokai Tenkaichi 3 | PCSX2 |

- [ ] **Step 2: Record which fail and why**

Expected problem areas, in order of likelihood:
- **RetroArch core paths.** Only two cores are installed (`mgba_libretro.so`, `mupen64plus_next_libretro.so`) at `~/.var/app/org.libretro.RetroArch/config/retroarch/cores/`. ES-DE's default find rules may not look inside the Flatpak's config dir.
- **Flatpak sandbox.** The emulators need `--filesystem=/mnt/internal/arcade`, already granted. ES-DE itself is unsandboxed and needs nothing.
- **`.ciso` extension.** Included in Task 4's `<extension>` list; confirm Dolphin actually accepts it as an argument.

- [ ] **Step 3: Fix launch commands per system as needed**

Add or amend `<command>` entries in `~/ES-DE/custom_systems/es_systems.xml` using the Flatpak form:

```xml
<command label="RetroArch">flatpak run org.libretro.RetroArch -L /home/jay/.var/app/org.libretro.RetroArch/config/retroarch/cores/mgba_libretro.so %ROM%</command>
```

- [ ] **Step 4: Re-verify all five, then commit**

All five must launch before proceeding. Copy the updated file to `mediahub-configs/es-de/` and commit.

---

### Task 6: Exit bindings — return to ES-DE every time

The chosen UX is "always back in ES-DE". That requires each emulator to **quit**, not merely close the game.

- [ ] **Step 1: PCSX2 — confirm the pause menu exits the process**

`OpenPauseMenu = SDL-0/Guide` already exists. Verify Guide → Exit terminates `pcsx2-qt` rather than returning to its game list.

```bash
pgrep -af pcsx2-qt   # expected: no output after exiting
```

- [ ] **Step 2: Dolphin — add a pad-reachable quit**

Dolphin has no such binding today. Configure a hotkey to Stop-and-exit, or rely on `-b -e` (from Task 4) which makes Dolphin exit when the game stops. Verify:

```bash
pgrep -af dolphin-emu   # expected: no output after quitting
```

- [ ] **Step 3: RetroArch — bind the exit hotkey to the pad**

RetroArch uses **udev**, so it sees `Sunshine X-Box One (virtual) pad`, not the SDL name. Set `input_exit_emulator_btn` plus a hotkey-enable button in `retroarch.cfg`.

```bash
grep -E "input_exit_emulator|input_enable_hotkey" \
  ~/.var/app/org.libretro.RetroArch/config/retroarch/retroarch.cfg
```

- [ ] **Step 4: Verify the full loop for all five systems**

ES-DE → game → quit via pad → back in ES-DE, with no keyboard or mouse touched.

**⚠️ Framebuffer capture no longer works — corrected 2026-08-17.** Input can still
be *injected* headlessly (`/dev/uinput` is writable, so a synthetic Xbox-VID/PID
pad can be created and driven from a script), but the **result can no longer be
observed from the host**. X11 screen capture on `:1` returns near-black on the
NVIDIA driver: with the root set to `#00FF00`, `ffmpeg -f x11grab` read `020002`
while the projector showed green. This is a property of the NVIDIA driver path,
**not** of `xf86-video-dummy` — fitting the plug did not fix it, and the old
technique worked only because the dummy driver kept its framebuffer in RAM.

**Consequence:** only NvFBC sees the screen, and NvFBC's output goes to the
Moonlight client. Verification of anything visual requires a human at the
projector. Substitute host-side evidence where possible — emulator logs (PCSX2's
`emulog.txt` shows ELF loads and CRCs), process liveness, and
`nvidia-smi --query-gpu=encoder.stats.sessionCount,encoder.stats.averageFps`.

**Time injected presses to the game, not the launch** — pressing during boot
looks exactly like a broken binding.

- [ ] **Step 5: Commit**

Copy `retroarch.cfg` and any Dolphin config into `mediahub-configs`, commit, push.

---

### Task 7: Hide PS3

- [ ] **Step 1: Confirm PS3 currently appears**

ES-DE should show a PlayStation 3 system with God of War Collection and Infamous — both unplayable (RPCS3 not installed, deferred to Phase 4).

- [ ] **Step 2: Mark both games hidden**

Via ES-DE's metadata editor per game, or directly:

```bash
grep -c "<hidden>true</hidden>" /home/jay/ES-DE/gamelists/ps3/gamelist.xml
```

Expected: `2`.

- [ ] **Step 3: Verify the system disappears and the toggle restores it**

Confirm the exact menu path for **"Show hidden games"** in the installed build and **write it into the design doc** — Jay specifically asked how to unhide.

- [ ] **Step 4: Document and commit**

Add the confirmed menu path to `es-de-frontend-design.md` under "Hiding PS3 and Switch". Commit.

---

### Task 8: Scrape artwork

**Prerequisite:** ScreenScraper account exists (created 2026-08-17).

- [ ] **Step 1: Jay enters credentials directly in ES-DE**

Menu → Scraper → Account settings. **Jay types these himself** — they must not pass through the session transcript or any tool call.

- [ ] **Step 2: Confirm the library is clean first**

```bash
find /mnt/internal/arcade/roms -maxdepth 2 -type f -name '*.7z' ! -path '*/ps3/*'
```

Expected: **no output**. If Task 1 Step 2 was skipped, stop — scraping now bakes in duplicates.

- [ ] **Step 3: Run the scrape**

Nine titles. Minutes, not hours; rate limits are not a factor at this size.

- [ ] **Step 4: Verify media landed**

```bash
find /home/jay/ES-DE/downloaded_media -type f | wc -l
find /home/jay/ES-DE/downloaded_media -type d -maxdepth 1
```

Expected: a non-trivial file count and per-system directories.

---

### Task 9: Add the Sunshine tile

**Files:**
- Modify: `/home/jay/.config/sunshine/apps.json`
- Modify: `mediahub-configs/sunshine/apps.json` (mirror)

- [ ] **Step 1: Back up apps.json**

```bash
cp ~/.config/sunshine/apps.json ~/.config/sunshine/apps.json.bak-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: Add the Arcade entry alongside the existing three**

Do **not** remove RetroArch, Dolphin or PCSX2 — the additive design is the rollback path.

```json
{
  "name": "Arcade (ES-DE)",
  "cmd": "/home/jay/Applications/ES-DE_x64.AppImage"
}
```

- [ ] **Step 3: Restore `-bigpicture` on the PCSX2 entry**

Outstanding follow-up now that it works mid-stream:

```json
{
  "name": "PCSX2 (PS2)",
  "cmd": "flatpak run net.pcsx2.PCSX2 -bigpicture -fullscreen"
}
```

- [ ] **Step 4: Validate the JSON before restarting Sunshine**

```bash
python3 -c "import json; d=json.load(open('/home/jay/.config/sunshine/apps.json')); print([a['name'] for a in d['apps']])"
```

Expected: five entries — Desktop, RetroArch, Dolphin, PCSX2, Arcade.

- [ ] **Step 5: Restart Sunshine and confirm the tile appears**

```bash
sudo systemctl restart sunshine
sleep 5 && systemctl is-active sunshine
```

Jay refreshes Moonlight and confirms **Arcade** is listed.

- [ ] **Step 6: End-to-end test from the projector**

Launch Arcade from Moonlight → pick a game per system → play → quit → back in ES-DE → quit ES-DE → stream ends cleanly.

---

### Task 10: Mirror configs, scrubbed

**⚠️ `mediahub-configs` is PUBLIC.**

- [ ] **Step 1: Find every credential in ES-DE's settings**

```bash
grep -inE "screenscraper|password|user" /home/jay/ES-DE/settings/es_settings.xml
```

- [ ] **Step 2: Copy config, scrub credentials**

Copy `es_settings.xml` to `mediahub-configs/es-de/` with the ScreenScraper username and password fields **emptied**, not merely renamed.

- [ ] **Step 3: Verify before committing, not after**

```bash
cd ~/mediahub-configs
git add -A
git diff --cached | grep -inE "screenscraper|password" 
```

Expected: field names may appear; **no values**. If any value shows, unstage and scrub again.

- [ ] **Step 4: Commit and push**

```bash
git commit -m "es-de: mirror config (credentials scrubbed)"
git push origin main
```

- [ ] **Step 5: Update the docs**

Mark ES-DE complete in `~/mediahub-arcade.md`, stamped with the date per that file's convention. Note the "collapse to a single Sunshine tile" follow-up as deliberately deferred.

---

## Self-review notes

- **Spec coverage:** install (T2), library wiring + GameCube override (T4), launch/exit model (T5, T6), Sunshine integration (T9), scraping (T8), hiding PS3 (T7), verification incl. the OpenGL risk (T3), credential handling (T10). Library hygiene (T1) is new — discovered during planning, and blocks T8.
- **Risk ordering:** the OpenGL mid-stream gate is Task 3, before any configuration investment.
- **Not covered, deliberately:** hardware-GL benchmarking (worth doing, but measurement rather than delivery); collapsing to one tile; RPCS3/PS3; theme customisation.
