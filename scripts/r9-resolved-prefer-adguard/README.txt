# Installed on mediahub-r9 (2026-09-21)
# script -> /usr/local/sbin/resolved-prefer-adguard.sh (chmod 755)
# units  -> /etc/systemd/system/; then: systemctl daemon-reload && systemctl enable --now resolved-prefer-adguard.timer
# Why: systemd-resolved never returns to its first DNS server after a failure (see docs/fleet-architecture.md, Q9).
