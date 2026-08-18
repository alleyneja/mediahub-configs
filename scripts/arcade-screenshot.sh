#!/usr/bin/env bash
# Headless visual verification for the arcade display (:1) on the Quadro P400.
#
# Captures what is actually on screen and reports enough to tell "the app is
# drawing" from "the screen is blank" without a human at the projector.
#
# Why this works when it previously did not: X11 capture on :1 was never broken.
# The X screen saver blanks after 600s of no INPUT (client drawing does not reset
# it), and a blanked screen returns a uniform frame from every capture path --
# x11grab, xwd and NvFBC alike. That was misread as "capture is dead on NVIDIA".
# Blanking is now disabled in /etc/X11/xorg-p400-headless.conf (BlankTime 0).
# If a capture ever comes back uniform again, check `xset q` FIRST.
#
# Usage: arcade-screenshot.sh [output.png]
set -uo pipefail

OUT="${1:-/tmp/arcade-$(date +%Y%m%d-%H%M%S).png}"
export DISPLAY=:1
export XAUTHORITY=/etc/X11/headless.xauth

command -v ffmpeg  >/dev/null || { echo "ffmpeg not installed"; exit 1; }
command -v xwininfo >/dev/null || { echo "x11-utils not installed"; exit 1; }

if ! xdpyinfo >/dev/null 2>&1; then
    echo "FAIL: cannot reach display :1 -- is xorg-headless.service running?"
    exit 1
fi

# A non-zero screen-saver timeout means the screen can blank under you and every
# capture below becomes meaningless. Warn loudly rather than returning a lie.
SAVER=$(xset q 2>/dev/null | awk '/timeout:/{print $2; exit}')
[ "${SAVER:-0}" != "0" ] && \
    echo "WARNING: screen saver timeout is ${SAVER}s, not 0 -- a blank capture may just be the saver."

ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1920x1080 -i :1 \
       -frames:v 1 -y "$OUT" 2>/dev/null || { echo "FAIL: capture failed"; exit 1; }

echo "saved: $OUT"
echo
echo "mapped windows on :1:"
xwininfo -root -children 2>/dev/null \
    | awk '/^     0x/ && $0 !~ /1x1\+/ {print "  " $0}' \
    | grep -v "10x10+10+10" || echo "  (none of interest)"
echo
echo "colour histogram (top 6):"
ffmpeg -hide_banner -loglevel error -i "$OUT" -vf format=rgb24 -f rawvideo - 2>/dev/null \
| python3 -c '
import sys, collections
d = sys.stdin.buffer.read()
px = [d[i:i+3] for i in range(0, len(d), 3)]
total = len(px)
c = collections.Counter(px)
for k, v in c.most_common(6):
    print(f"  #{k.hex()}  {v:>9}  {100*v/total:5.1f}%")
top, topn = c.most_common(1)[0]
print()
if topn / total > 0.995:
    print(f"VERDICT: UNIFORM FRAME -- {100*topn/total:.2f}% of the screen is #{top.hex()}.")
    print("         No application is drawing. #000000 usually means the screen saver blanked")
    print("         (check `xset q`); any other flat colour usually means a bare root window.")
elif len(c) < 8:
    print(f"VERDICT: NEARLY BLANK -- only {len(c)} distinct colours. Probably a solid background, no content.")
else:
    print(f"VERDICT: CONTENT PRESENT -- {len(c)} distinct colours, largest region {100*topn/total:.1f}%.")
'
