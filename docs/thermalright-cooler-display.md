# r9 cooler display (Thermalright Peerless Assassin 120 Digital)

The CPU cooler on **mediahub-r9** has a segment display on top. It is a USB device, `0416:8001`
("Winbond LED Controller"), and it shows nothing until software feeds it. It showed only a spinning
circle until set up on 2026-10-20.

## Setup (r9 only)
- Driver: [TRCC Linux](https://github.com/Lexonight1/thermalright-trcc-linux) 9.10.9, installed with
  `pipx install trcc-linux` (apt: `pipx sg3-utils`). It lists `0416:8001` as PA120 DIGITAL.
- udev: `system/99-trcc-lcd.rules` -> `/etc/udev/rules.d/`. **Deliberately minimal.** `trcc system setup`
  also makes the RAPL energy counter world-readable (CVE-2020-8694) and adds storage quirks for LCDs
  we don't have, so it was not used.
- Service: `systemd/user/trcc.service` -> `~/.config/systemd/user/`, enabled, plus
  `loginctl enable-linger jay` so it starts at boot without a login.
- Settings: `trcc led mode 0416:8001 TEMP_LINKED`, `trcc config temp-unit F` (stored in `~/.trcc/trcc.json`).

## Notes
- Display is 84 segments / 4 zones, not a full LCD. It carousels CPU and GPU temp/usage.
- Case-fan RPM reads 0 in TRCC; the Gigabyte board does not expose fan tach to Linux.
- Revert: `pipx uninstall trcc-linux`, delete the udev rule, `systemctl --user disable --now trcc`.
