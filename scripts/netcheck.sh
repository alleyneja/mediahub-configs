#!/usr/bin/env bash
# netcheck.sh - repeatable network baseline for mediahub.
#
# Purpose: make "did that fix it?" answerable instead of a feeling.
# Run it, change ONE thing (e.g. restart the modem), run it again, diff.
#
#   ~/netcheck.sh                 # run and append to ~/netcheck.log
#   ~/netcheck.sh --compare       # show the last two runs side by side
#
# Written 2026-08-21 chasing Aurral slowness on cellular.

LOG=/home/jay/netcheck.log
PHONE=100.97.193.57      # jays-iphone on the tailnet
ROUTER=192.168.0.1

if [ "$1" = "--compare" ]; then
    awk '/^=== RUN/{n++} n>=c-1' c=$(grep -c '^=== RUN' "$LOG") "$LOG" 2>/dev/null | tail -60
    exit 0
fi

exec > >(tee -a "$LOG") 2>&1
echo
echo "=== RUN $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

pingstat() {
    local target=$1 label=$2 count=${3:-30}
    local out
    out=$(timeout 60 ping -c "$count" -i 0.2 -W 2 "$target" 2>/dev/null | tail -2)
    local loss rtt
    loss=$(echo "$out" | grep -oE '[0-9.]+% packet loss' | head -1)
    rtt=$(echo "$out"  | grep -oE 'min/avg/max/mdev = [0-9./]+' | sed 's|min/avg/max/mdev = ||')
    printf "  %-22s loss=%-8s rtt(min/avg/max/jitter)=%s\n" "$label" "${loss:-n/a}" "${rtt:-n/a}"
}

echo "-- reachability (ICMP; note ISPs deprioritise ping, so trust TCP below more)"
pingstat "$ROUTER" "router (LAN)"
pingstat 8.8.8.8   "internet 8.8.8.8"
pingstat "$PHONE"  "jays-iphone (tailnet)" 40

echo "-- tailscale path to phone (direct vs DERP relay matters a lot)"
timeout 20 tailscale ping --c 3 "$PHONE" 2>&1 | tail -3 | sed 's/^/  /'

echo "-- real TCP throughput + retransmits (the number that actually matters)"
b_o=$(nstat -az TcpOutSegs 2>/dev/null | awk 'NR==2{print $2}')
b_r=$(nstat -az TcpRetransSegs 2>/dev/null | awk 'NR==2{print $2}')
dl=$(curl -s -o /dev/null -w '%{speed_download}' --max-time 60 \
      'https://speed.cloudflare.com/__down?bytes=25000000' 2>/dev/null)
a_o=$(nstat -az TcpOutSegs 2>/dev/null | awk 'NR==2{print $2}')
a_r=$(nstat -az TcpRetransSegs 2>/dev/null | awk 'NR==2{print $2}')
python3 -c "
dl=float('${dl:-0}'); o=$((a_o-b_o)); r=$((a_r-b_r))
print(f'  download              {dl/125000:.1f} Mbps   retrans={r*100/o:.2f}%' if o else '  download  failed')"

echo "-- latency UNDER load (bufferbloat: a big jump here is the gateway choking)"
curl -s -o /dev/null --max-time 25 'https://speed.cloudflare.com/__down?bytes=150000000' >/dev/null 2>&1 &
J=$!
pingstat 8.8.8.8 "8.8.8.8 (loaded)" 20
wait $J 2>/dev/null

echo "-- what a phone actually pulls from Aurral"
echo "  discover page: 246 requests / ~14.2 MB of artwork (measured 2026-08-21)"
