#!/usr/bin/env bash
# pathtest.sh - reach mediahub from a phone WITHOUT Tailscale, to separate
# "the tunnel is slow" from "the path to this house is slow".
#
# Uses TCP 41641 because your router ALREADY forwards it (the
# tailscaleMediahubStatic rule, TCP/UDP 41641 -> 192.168.0.21) and tailscaled
# has since moved to UDP 4500, leaving the TCP side free. No router change,
# and a short IPv4 URL instead of a 39-character IPv6 one.
#
# Safety:
#   - serves ONLY the static speedtest files
#   - firewall opened ONLY to T-Mobile CGNAT space (172.56.0.0/14), not the world
#   - auto-teardown after 15 min, on Ctrl-C, or on any exit
#
# Run:  sudo ~/mediahub-configs/scripts/pathtest.sh
set -u
PORT=41641
DIR=/srv/docker/caddy/config/speedtest
TMO="172.56.0.0/14"

cleanup() {
  echo
  echo "--- tearing down ---"
  [ -n "${SRV:-}" ] && kill "$SRV" 2>/dev/null
  ufw delete allow from "$TMO" to any port $PORT proto tcp >/dev/null 2>&1
  left=$(ufw status | grep -c "$PORT/tcp")
  echo "  server stopped"
  echo "  leftover tcp/$PORT rules: $left (want 0)"
}
trap cleanup EXIT INT TERM

echo "=== preflight ==="
if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
  echo "  ERROR: something already listens on TCP $PORT - aborting"; exit 1
fi
echo "  TCP $PORT is free"
echo "  tailscaled UDP port: $(ss -ulnp 2>/dev/null | grep tailscaled | grep -oE ':[0-9]+' | head -1 | tr -d ':')"

python3 - "$DIR" "$PORT" <<'PY' &
import http.server, socketserver, os, sys
os.chdir(sys.argv[1])
class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, f, *a):
        with open('/tmp/pathtest-access.log', 'a') as fh: fh.write((f % a) + '\n')
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store'); super().end_headers()
socketserver.TCPServer.allow_reuse_address = True
socketserver.TCPServer(('0.0.0.0', int(sys.argv[2])), H).serve_forever()
PY
SRV=$!
sleep 2

ufw allow from "$TMO" to any port $PORT proto tcp comment 'TEMP pathtest' >/dev/null 2>&1

WAN=$(curl -s4 --max-time 10 https://api.ipify.org)
echo
echo "=== self-test (local) ==="
curl -s -o /dev/null -w "  net.html : %{http_code} %{size_download}B\n" "http://127.0.0.1:$PORT/net.html"
curl -s -o /dev/null -w "  4mb.bin  : %{http_code} %{size_download}B in %{time_total}s\n" "http://127.0.0.1:$PORT/4mb.bin"
echo "  firewall: open only to $TMO (T-Mobile), not the internet at large"
echo
echo "  ================================================================"
echo "  On your phone, on CELLULAR, open:"
echo
echo "        http://${WAN:-<no-wan-ip>}:$PORT/net.html"
echo
echo "  Tailscale can stay ON - this bypasses it either way."
echo "  ================================================================"
echo
echo "  Auto-teardown in 15 minutes. Ctrl-C to stop sooner."
sleep 900
