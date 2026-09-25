#!/bin/bash
# Once-a-minute stat of ~200 "sentinel" files that are KNOWN to flip to mode 000 in NFS clients' view
# (they did in the 2026-09-25 ~08:27 spike, which lasted only minutes and fell between the monitor's 15-min scans).
# Half were chmod'd 644 (treated), half left at 777 (control). Logs mode counts per group so the next spike gets exact
# start/end times and shows whether explicit mode protects against it. Read-only (stat only). Manifests live in
# /home/jay/logs (not in the public repo). Remove the cron line when the investigation closes.
D=/home/jay/logs
out="$(date -u +%Y-%m-%dT%H:%M:%SZ) host=$(hostname)"
for g in treated control; do
  f="$D/sentinels-$g.txt"; [ -f "$f" ] || continue
  counts=$(while IFS= read -r p; do timeout 5 stat -c %a "$p" 2>/dev/null || echo gone; done < "$f" | sort | uniq -c | awk '{printf "%s=%s ", $2, $1}')
  out="$out $g: ${counts}"
done
echo "$out" >> "$D/permissions-sentinel.log"
