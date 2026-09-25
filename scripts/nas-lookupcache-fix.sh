#!/bin/bash
# D12 fix (2026-09-25): mount the NAS NFS export with lookupcache=none.
# UGREEN's ugacl kernel mis-renders the mode (000/700) of inodes the NAS reloads by file handle after evicting them
# under memory pressure; lookups by name render correctly. lookupcache=none makes the client look up by name on
# every access. Reproduced + verified: after pressure, /mnt/media 0/200 readable vs lookupcache=none 200/200.
# Applies live by mounting a lookupcache=none instance ON TOP of /mnt/nas (mergerfs resolves by path, so new opens
# use it; already-open files finish on the old mount). fstab is updated so the next boot mounts it cleanly.
# Run on each host that mounts the NAS (production, r9). Idempotent.
set -euo pipefail
FSTAB=/etc/fstab
if ! grep -qE '^[^#]*:/volume1/media[[:space:]]+/mnt/nas[[:space:]]+nfs[[:space:]]' "$FSTAB"; then
  echo "no /mnt/nas NFS line in $FSTAB on $(hostname)"; exit 1
fi
if grep -qE '^[^#]*/mnt/nas[[:space:]]+nfs[[:space:]]+[^[:space:]]*lookupcache=none' "$FSTAB"; then
  echo "fstab already has lookupcache=none"
else
  sudo cp "$FSTAB" "$FSTAB.bak-$(date +%Y%m%d-%H%M%S)"
  sudo sed -i -E 's%^([^#]*:/volume1/media[[:space:]]+/mnt/nas[[:space:]]+nfs[[:space:]]+)%\1lookupcache=none,%' "$FSTAB"
  echo "fstab updated:"; grep -E '/mnt/nas[[:space:]]+nfs' "$FSTAB"
fi
if awk '$2=="/mnt/nas"{print $4}' /proc/mounts | tail -1 | grep -q lookupcache=none; then
  echo "live /mnt/nas already lookupcache=none"
else
  src=$(awk '$2=="/mnt/nas"{print $1}' /proc/mounts | tail -1)
  sudo mount -t nfs -o rw,vers=3,hard,timeo=600,retrans=5,lookupcache=none,nosharecache "$src" /mnt/nas
  echo "overmounted /mnt/nas:"; awk '$2=="/mnt/nas"{print "  "$4}' /proc/mounts
fi
L=/mnt/media/permlab-20260925
if [ -d "$L" ]; then
  ok=0; bad=0
  for f in "$L"/f1[89]??; do if head -c2 "$f" >/dev/null 2>&1; then ok=$((ok+1)); else bad=$((bad+1)); fi; done
  echo "verify via /mnt/media (lab files 1800-1999): read OK=$ok DENIED=$bad"
fi
