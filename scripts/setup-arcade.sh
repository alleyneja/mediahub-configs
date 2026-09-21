#!/usr/bin/env bash
# setup-arcade.sh -- build the arcade / emulation / game-streaming half of a machine.
#
# setup.sh (repo root) builds the Docker service stacks. It does NOT touch emulators,
# ES-DE or Sunshine; this script does. Written 2026-09-20 while migrating that half from
# mediahub-i7 (today mediahub-production) to mediahub-r9. Run one step at a time:
#
#   ./setup-arcade.sh flatpaks        # emulators, pinned to the builds i7 ran
#   ./setup-arcade.sh sunshine        # Sunshine, from the .deb archived with the emulators
#   ./setup-arcade.sh rpcs3           # PS3 firmware (official, from Sony) into the RPCS3 flatpak
#   ./setup-arcade.sh wiimote         # udev rules, input group, dongle-only Bluetooth (r9)
#
# Every step is idempotent. Later steps are appended below as they are proven on r9.
set -euo pipefail

ARCADE_ROOT=/mnt/internal/arcade

# Exact Flathub commits running on i7 on 2026-09-20. Pinned so a rebuilt machine plays the
# same emulator versions, not whatever "stable" has moved to. If Flathub no longer serves a
# commit the step falls back to current stable and says so.
declare -A FLATPAKS=(
  [org.DolphinEmu.dolphin-emu]=377c3e63506ebc51c6eb8ae717f216514f3fdbdd634d69c00c4c0173fb0629e5  # 2606
  [net.pcsx2.PCSX2]=31307c3e9fa0fda4275433c053169dd231a7f921bb80bb51dd67b2ef95638f28             # v2.6.3
  [org.libretro.RetroArch]=1f766799d9ffffd822b8d9d2ceda6368c622aa1dfba495ce9b99b7b636a37f10       # 1.22.2
  [io.github.ryubing.Ryujinx]=096c5ffb45b6f6781b0a9bccd171f45557300b915e8105ded258437b85cdb467   # 1.3.3
  [net.rpcs3.RPCS3]=62f5a1f7ae8f6dae9e30cb0cb84413c322f652af9a3b9b6ccce04f8d82c2a5dd             # 0.0.42-20022, added 2026-09-20
)

need_arcade_root() {
  [ -d "$ARCADE_ROOT" ] || { echo "ERROR: $ARCADE_ROOT missing (mount the games drive at /mnt/internal first)"; exit 1; }
}

step_flatpaks() {
  need_arcade_root
  command -v flatpak >/dev/null || sudo apt-get install -y flatpak
  sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  for app in "${!FLATPAKS[@]}"; do
    sudo flatpak install -y --noninteractive flathub "$app"
    if ! sudo flatpak update -y --noninteractive --commit="${FLATPAKS[$app]}" "$app"; then
      # Flathub only keeps recent commits (2026-09-20: PCSX2 v2.6.3 and RetroArch 1.22.2 were
      # already gone). Use the bundle exported from i7 and archived in $ARCADE_ROOT/emulators,
      # made with: flatpak build-bundle /var/lib/flatpak/repo <app>-prod.flatpak <app> stable
      bundle="$ARCADE_ROOT/emulators/$app-prod.flatpak"
      if [ -f "$bundle" ]; then
        sudo flatpak install -y --noninteractive --bundle "$bundle"
      else
        echo "WARN: pinned commit for $app gone and no bundle at $bundle; left on current stable"
      fi
    fi
    # The only override i7 uses: let the sandbox read/write the library and saves.
    sudo flatpak override --filesystem="$ARCADE_ROOT" "$app"
  done
  echo "--- installed:"; flatpak list --app --columns=application,version,active
}

step_sunshine() {
  need_arcade_root
  # Same build i7 ran (0.0.0-14ffa6f-dirty, source unknown -- packaged from i7 with
  # dpkg-repack), archived beside the emulators so it survives upstream moving on.
  sudo apt-get install -y "$ARCADE_ROOT"/emulators/sunshine_*.deb
  sunshine --version 2>&1 | head -1
}

step_rpcs3() {
  # Firmware is the official Sony PS3UPDAT.PUP (v4.93 as of 2026-09-20; Sony's page
  # https://www.playstation.com/en-us/support/hardware/ps3/system-software/ has the current URL).
  # Only the official file works. RPCS3 asks "Install firmware?" in a Qt dialog even when given
  # --installfw, so run it on a private virtual display and click Yes; a real display would pop
  # the dialog over the streamed screen. Needs: xvfb xdotool.
  need_arcade_root
  local pup="$ARCADE_ROOT/bios/ps3/PS3UPDAT.PUP" url="${PS3_PUP_URL:-http://dus01.ps3.update.playstation.net/update/ps3/image/us/2026_0318_a2b60b6ac1d2e49e230144345616927c/PS3UPDAT.PUP}"
  local flash="$HOME/.var/app/net.rpcs3.RPCS3/config/rpcs3/dev_flash"
  if [ "$(find "$flash" -type f 2>/dev/null | wc -l)" -gt 1000 ]; then echo "firmware already installed"; return; fi
  mkdir -p "$(dirname "$pup")"
  [ -f "$pup" ] || curl -fL -o "$pup" "$url"
  head -c 5 "$pup" | grep -q SCEUF || { echo "ERROR: $pup is not a PS3 update file"; exit 1; }
  command -v xvfb-run >/dev/null && command -v xdotool >/dev/null || sudo apt-get install -y xvfb xdotool
  Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 & local xpid=$!
  sleep 2
  DISPLAY=:99 flatpak run net.rpcs3.RPCS3 --installfw "$pup" >/dev/null 2>&1 & local rpid=$!
  sleep 25; DISPLAY=:99 xdotool mousemove 534 239 click 1     # the "Yes" button
  for _ in $(seq 1 60); do sleep 3; [ "$(find "$flash" -type f | wc -l)" -gt 1000 ] && break; done
  sleep 5; kill $rpid $xpid 2>/dev/null || true
  echo "dev_flash files: $(find "$flash" -type f | wc -l) (expect ~1075)"
}

step_wiimote() {
  # Real Wii Remotes under Dolphin. Proven on r9 2026-09-20 (Wii Sports, Mario Party 9).
  local here; here="$(cd "$(dirname "$0")/.." && pwd)"
  sudo install -m 644 "$here/system/72-wiimote.rules" /etc/udev/rules.d/72-wiimote.rules
  # r9 only: it has a second, built-in Bluetooth radio that Dolphin wrongly picks and that
  # cannot see the remote. Skip on machines with a single adapter.
  if lsusb | grep -q '0bda:b850' && lsusb | grep -q '0b05:1bf6'; then
    sudo install -m 644 "$here/system/73-r9-bluetooth-dongle-only.rules" /etc/udev/rules.d/73-r9-bluetooth-dongle-only.rules
  fi
  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb
  sudo usermod -aG input "$USER"   # takes effect at next login; i7's jay is in it too
  echo "Sync remotes with 1+2 (never the red button). Do NOT toggle adapters while Dolphin runs -- it hangs on exit."
}

case "${1:-}" in
  rpcs3) step_rpcs3 ;;
  wiimote) step_wiimote ;;
  flatpaks) step_flatpaks ;;
  sunshine) step_sunshine ;;
  *) echo "usage: $0 flatpaks|sunshine|rpcs3|wiimote"; exit 2 ;;
esac
