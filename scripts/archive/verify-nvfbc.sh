#!/usr/bin/env bash
# Post-reboot verification for the P400 + NvFBC streaming stack.
# Run AFTER rebooting. Every check should print OK.
# Stops at the first failure so you don't chase a downstream symptom.

pass=0; fail=0
chk() { # chk "label" "expected" "actual"
  if [ "$2" = "$3" ]; then printf '  OK    %-38s %s\n' "$1" "$3"; pass=$((pass+1))
  else printf '  FAIL  %-38s got=%s want=%s\n' "$1" "$3" "$2"; fail=$((fail+1)); fi
}

echo "=== 1. kernel / module state ==="
chk "nvidia_drm modeset" "N" "$(sudo cat /sys/module/nvidia_drm/parameters/modeset 2>/dev/null)"
chk "gdm3 running" "inactive" "$(systemctl is-active gdm3.service 2>/dev/null)"
chk "display-manager.service" "absent" \
    "$(test -e /etc/systemd/system/display-manager.service && echo present || echo absent)"

echo
echo "=== 2. services ==="
for s in xorg-headless openbox-headless sunshine; do
  chk "$s" "active" "$(systemctl is-active $s.service)"
done

echo
echo "=== 3. Xorg is on the P400, not the dummy ==="
conf=$(pgrep -a Xorg | grep ':1' | grep -oE 'xorg-[a-z0-9-]+\.conf')
chk "xorg :1 config" "xorg-p400-headless.conf" "$conf"
echo "  -- displays Sunshine sees (want DP-5 / a real name, NOT DUMMY0) --"
sudo journalctl -u sunshine.service -b --no-pager 2>/dev/null \
  | grep -i 'Detected display' | head -5 | sed 's/^/     /'

echo
echo "=== 4. NvFBC selected, no modeset error ==="
if sudo journalctl -u sunshine.service -b --no-pager 2>/dev/null | grep -qi 'display server is in modeset'; then
  echo "  FAIL  modeset error still present in this boot's log"; fail=$((fail+1))
else
  echo "  OK    no 'display server is in modeset' this boot"; pass=$((pass+1))
fi
sudo journalctl -u sunshine.service -b --no-pager 2>/dev/null \
  | grep -iE "config: 'capture'|Screencasting with|Found H.264|Found HEVC" | tail -5 | sed 's/^/     /'

echo
echo "=== 5. hardware GL actually live on :1 ==="
DISPLAY=:1 XAUTHORITY=/etc/X11/headless.xauth glxinfo 2>/dev/null \
  | grep -iE 'OpenGL renderer|OpenGL vendor' | sed 's/^/     /' \
  || echo "     (glxinfo not installed: sudo apt install mesa-utils)"

echo
printf '=== %d passed, %d failed ===\n' "$pass" "$fail"
[ "$fail" -eq 0 ] && echo "Stack looks good. Launch Dolphin from Moonlight and watch:" \
  && echo "  sudo journalctl -u sunshine.service -f"
exit "$fail"
