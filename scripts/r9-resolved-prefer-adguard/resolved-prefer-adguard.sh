#!/bin/bash
# systemd-resolved never returns to its first-listed DNS server after a failure, so after any AdGuard hiccup
# (rate-limit drops, production reboots Wed/Sun 03:00) this box stays on 1.1.1.1 and loses every .lan name.
# If it has drifted but AdGuard answers a .lan query again, restart resolved so it starts from AdGuard.
ADGUARD=192.168.0.21
cur=$(resolvectl status | awk -F": " "/Current DNS Server/{print \$2; exit}")
[ "$cur" = "$ADGUARD" ] && exit 0
if [ -n "$(dig +short +time=2 +tries=1 plex.lan @$ADGUARD 2>/dev/null)" ]; then
  logger -t resolved-prefer-adguard "resolver drifted to ${cur:-none}; AdGuard answers again -> restarting systemd-resolved"
  systemctl restart systemd-resolved
fi
