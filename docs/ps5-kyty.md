# PS5 on r9 (KytyPS5)

Set up 2026-10-22. First game: Astro Bot (PPSA21564, v01.007.000). It reaches its title screen on the pinned
build below. Not yet verified by Jay: controls, gameplay, FPS, quitting from ES-DE.

## Pieces

| What | Where |
|---|---|
| Emulator builds | `~/Applications/kyty/<tag>/` (official `KytyPS5/KytyPS5` Linux x86_64 release tarballs) |
| Active build | `~/Applications/kyty/current` symlink, **pinned to `KytyPS5-2026-10-01-4479808`** |
| Games (dumped folders) | `/mnt/internal/arcade/roms/ps5/<PPSAxxxxx>/` (NVMe, ext4) |
| ES-DE ROM stubs | `/mnt/internal/arcade/roms/ps5/<Name>.ps5`, one line = the serial |
| Launcher | `scripts/kyty-launch` -> `~/.local/bin/kyty-launch`; hold or double-tap the Xbox button to quit |
| ES-DE system | `es-de/custom_es_systems.xml`, system `ps5` |
| Kyty working dir | `~/.local/share/kyty/` (`_SaveData`, `_PipelineCache`, `_Textures`, ...): saves live here |

## Why the build is pinned

Astro Bot crashes at startup on the 2026-10-22 builds (tested `f5f4e7d`): a null-pointer copy in guest code,
identical with `--amd-cpu`. The Oct 1 build `4479808` (named as "last working" in Kyty's compatibility
report) reaches the title screen. Kyty ships several builds a day, so re-test before moving the symlink:

    cd ~/Applications/kyty && ln -sfn <tag> current

Old tags stay downloadable from the GitHub releases page. Newer builds take extra flags (`--perf-overlay`,
`--hide-cursor`) that the Oct 1 build rejects, so `kyty-launch` passes only `--fullscreen`. Add those back
if the pin moves to a build that supports them. `kyty_updater` ships in the tarball; do not run it.

## Adding a game

PS5 dumps are plain folders (`eboot.bin`, `sce_module/`, `sce_sys/`, `data/`). Astro Bot arrived as a 40 GB
RAR that unpacks to about 160 GB.

    unrar x -y -idq 'game.rar' /mnt/internal/arcade/roms/ps5/_tmp/
    mv /mnt/internal/arcade/roms/ps5/_tmp/<PPSAxxxxx-app> /mnt/internal/arcade/roms/ps5/<PPSAxxxxx>
    printf 'PPSAxxxxx\n' > '/mnt/internal/arcade/roms/ps5/Game Name.ps5'

`unrar` (nonfree, Ubuntu multiverse) is required. 7-Zip and bsdtar both fail on parts of these RAR5
files ("Unsupported Method" / "Unsupported block header size") and bsdtar aborts the whole extraction.
Check the Kyty compatibility list (kytyps5.github.io) for the title and platform before downloading.

## Gotchas

- Games run native guest code as the logged-in user. Run unfamiliar dumps with care (sandbox idea pending).
- ClamAV is installed (`clamscan`); it knows almost nothing about PlayStation code.
- Compatibility "In game" means controllable gameplay, with bugs and performance problems possible.
