# PS4 on r9 (shadPS4)

Set up 2026-10-21. First game: Dark Souls Remastered (CUSA08495), boots to title at 60 FPS, Xbox pad works
with no mapping. Next: FPS across real gameplay, more titles, 2-pad local play.

## Pieces

| What | Where |
|---|---|
| Emulator | `~/Applications/shadps4/shadps4-0.20.0.AppImage` (SDL core build; `shadps4.AppImage` is a symlink) |
| PKG extractor | `~/Applications/ps4-pkg-tool/ps4-pkg-tool` (xXJSONDeruloXx/ps4-pkg-tools, prebuilt Linux CLI) |
| Games (unpacked) | `/mnt/internal/arcade/roms/ps4/<CUSAxxxxx>/` (NVMe, ext4) |
| ES-DE ROM stubs | `/mnt/internal/arcade/roms/ps4/<Name>.ps4`, one line = the serial |
| Launcher | `scripts/ps4-launch` -> `~/.local/bin/ps4-launch`; hold or double-tap the Xbox button to quit |
| ES-DE system | `es-de/custom_es_systems.xml`, system `ps4` |
| shadPS4 user data | `~/.local/share/shadPS4/` (config, shader cache, saves, logs) |

## Adding a game

shadPS4 0.20.0 cannot install PKGs. The core build has no installer, and the QtLauncher has no
Install PKG menu either. Unpack with the extractor instead:

    ~/Applications/ps4-pkg-tool/ps4-pkg-tool <game.pkg> /mnt/internal/arcade/roms/ps4/_tmp
    mv /mnt/internal/arcade/roms/ps4/_tmp/CUSAxxxxx /mnt/internal/arcade/roms/ps4/ && rmdir /mnt/internal/arcade/roms/ps4/_tmp
    printf 'CUSAxxxxx\n' > '/mnt/internal/arcade/roms/ps4/Game Name.ps4'

The tool names the output folder after the serial. Dark Souls (7.3 GB PKG) took about 2 minutes.

Updates: unpack the update PKG the same way, then put it in a folder named `CUSAxxxxx-UPDATE` (or
`-patch`) next to the game folder; shadPS4 layers it on automatically (shadps4.net/quickstart). Not yet
tested here. DLC goes in `~/.local/share/shadPS4/addcont/<CUSAxxxxx>/`. Also untested.

Keep the unpacked games on `/mnt/internal`, not `/mnt/media`: the pool runs `cache.files=off`, which
blocks mmap (see gotcha in mediahub-issues notes), and games read thousands of small files.

## Gotchas

- `pkill -f` / `pgrep -f` over `ssh '...'` matches the ssh command line itself and kills the session.
  Use the `[s]hadps4` bracket trick.
- Closing the shadPS4 window or SIGTERM both exit cleanly.
- ES-DE has to be restarted to see a new system.
- The Bloodborne download that was tried is a modded build (`[60Fps-1080p]-[DebugMenu]`), not stock.
  Untested.
