#!/bin/bash
# Drives the parts of the D12 Section 2 controlled-reproduction pass that don't need
# Jay's hands: a same-content write from both production and r9 into a disposable test
# folder (isolates whether the extra NFS hop on r9 changes anything by itself), plus a
# Bazarr manual-download trigger against a designated test episode (the previously-named
# suspect write path). permissions-monitor.sh's next cron tick (or a manual run) is what
# actually catches anything this provokes - this script only provokes, it doesn't detect.
#
# Manual steps this script CANNOT do (see docs/permissions-monitor.md for the full list):
#   - an SMB file copy from the gaming PC into the same test folder
#   - a UGOS app action (e.g. Media Server re-index) on the NAS side
# Never run permissions-apply-fix.sh with no arguments as part of this - if you want to
# demonstrate the fix path on a synthetic file this script created, pass its exact path
# explicitly.
set -uo pipefail

TEST_DIR="/mnt/media/.permissions-repro-test"
mkdir -p "$TEST_DIR"

echo "== write from this host (hostname: $(hostname)) =="
echo "repro test $(date -u +%Y-%m-%dT%H:%M:%SZ) from $(hostname)" > "$TEST_DIR/local-write-$(hostname).txt"
stat -c '%a %n' "$TEST_DIR/local-write-$(hostname).txt"

if [ "$(hostname)" != "mediahub-r9" ]; then
    echo "== triggering the same write from r9 over SSH, for comparison =="
    ssh -o BatchMode=yes -o HostKeyAlias=192.168.0.149 jay@192.168.0.22 \
        "echo 'repro test from r9' > '$TEST_DIR/local-write-r9.txt'; stat -c '%a %n' '$TEST_DIR/local-write-r9.txt'"
fi

echo "== Bazarr manual-download trigger (edit EPISODE_ID before running) =="
echo "Not run automatically - this needs a real episode ID from the Bazarr UI/API."
echo "Example once you have one:"
echo "  curl -s 'http://127.0.0.1:6767/api/episodes/subtitles?episodeid=<ID>&language=es&forced=false&hi=false&original_format=false&provider=subdl' -H 'X-API-KEY: <key>'"

echo "== done. Run permissions-monitor.sh now (both hosts) to see if anything got flagged. =="
echo "== Clean up when finished: rm -rf $TEST_DIR (on whichever host currently sees it) =="
