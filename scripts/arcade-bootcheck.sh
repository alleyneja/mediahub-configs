#!/usr/bin/env bash
# Boot-verify a ROM headlessly: launch it, watch it actually execute, screenshot
# the result, shut it down.
#
#   ~/arcade-bootcheck.sh "/mnt/internal/arcade/roms/wii/Some Game.wbfs" [seconds]
#
# This is the standard to meet before deleting a redundant copy of a game. A file
# existing proves nothing; a file that reaches its own title screen proves the
# archive beside it is disposable. Established 2026-08-19 after it cleared three
# duplicates cleanly (Melee, Bratz, Wii Play - Motion).
#
# Verdict is PASS only if all three hold: the emulator is still alive at the end,
# it burned real CPU, and the framebuffer shows content rather than a blank screen.

set -uo pipefail

ROM="${1:-}"
WAIT="${2:-45}"
DISPLAY_NUM=":1"
SHOT_DIR="${SHOT_DIR:-/tmp/arcade-bootcheck}"

[[ -n "$ROM" ]] || { echo "usage: $(basename "$0") <rom-path> [seconds]" >&2; exit 2; }
[[ -f "$ROM" || -d "$ROM" ]] || { echo "no such ROM: $ROM" >&2; exit 2; }

mkdir -p "$SHOT_DIR"
base=$(basename "$ROM")
shot="$SHOT_DIR/${base%.*}.png"
log=$(mktemp)

# Pick the emulator the way ES-DE does: by system folder, not by extension.
# .iso means GameCube under ngc/ and PS2 under ps2/, so extension alone lies.
system=$(basename "$(dirname "$ROM")")
case "$system" in
    ngc|wii)
        procname=dolphin-emu
        cmd=(flatpak run org.DolphinEmu.dolphin-emu -b
             -C Dolphin.Display.Fullscreen=True -e "$ROM") ;;
    ps2)
        procname=pcsx2-qt
        cmd=(flatpak run net.pcsx2.PCSX2 -batch -fullscreen "$ROM") ;;
    *)
        echo "no standalone emulator mapped for system '$system'." >&2
        echo "RetroArch systems are core-dependent — verify those by hand." >&2
        exit 2 ;;
esac

echo "ROM:      $base"
echo "system:   $system  ->  $procname"
echo "watching: ${WAIT}s"
echo

gpu_before=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)

DISPLAY="$DISPLAY_NUM" nohup "${cmd[@]}" > "$log" 2>&1 &
sleep "$WAIT"

alive=no; cpu=0
if pgrep -x "$procname" >/dev/null; then
    alive=yes
    cpu=$(ps -eo pcpu,comm --no-headers | awk -v p="$procname" '$2==p {print int($1); exit}')
    cpu=${cpu:-0}
fi
gpu_after=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)

verdict_line=$("$HOME/arcade-screenshot.sh" "$shot" 2>&1 | grep -E '^VERDICT' || true)

# pkill -f would match this script's own command line, which contains the
# emulator name -- that self-kill cost two runs on 2026-08-19. Match the
# process name exactly instead.
pkill -x "$procname" 2>/dev/null
sleep 3
pkill -9 -x "$procname" 2>/dev/null

echo "still running: $alive"
echo "cpu:           ${cpu}%"
echo "gpu memory:    ${gpu_before} -> ${gpu_after} MiB"
echo "$verdict_line"
echo "screenshot:    $shot"
echo

if [[ "$alive" == yes && "$cpu" -ge 10 && "$verdict_line" == *"CONTENT PRESENT"* ]]; then
    echo "PASS -- it executed and drew a real frame. Look at the screenshot to confirm"
    echo "it is the game and not an emulator error dialog, then the archive is safe to delete."
    exit 0
fi

echo "FAIL -- did not clearly boot. Emulator log:"
tail -20 "$log"
exit 1
