#!/usr/bin/env bash
# Write ScreenScraper credentials into ES-DE's settings file without the values
# ever appearing on screen, in shell history, or in a Claude transcript.
#
#   ~/arcade-scraper-creds.sh
#
# ES-DE stores these in plaintext in es_settings.xml — that is ES-DE's design, not
# a choice made here. The file is NOT mirrored to mediahub-configs with values in
# it; the repo copy is scrubbed (see docs/es-de-frontend-plan.md, Task 10).

set -euo pipefail

SETTINGS="$HOME/ES-DE/settings/es_settings.xml"

[[ -f "$SETTINGS" ]] || { echo "no settings file at $SETTINGS — start ES-DE once first" >&2; exit 1; }

# ES-DE rewrites this file wholesale when it exits, silently discarding edits made
# while it is running.
if pgrep -f 'ES-DE.*AppImage' >/dev/null 2>&1; then
    echo "ES-DE is running — quit it first or it will overwrite these values on exit." >&2
    exit 1
fi

read -rp  "ScreenScraper username: " SS_USER
read -rsp "ScreenScraper password: " SS_PASS
echo

[[ -n "$SS_USER" && -n "$SS_PASS" ]] || { echo "both fields are required" >&2; exit 1; }

BACKUP="$SETTINGS.bak-$(date +%Y%m%d-%H%M%S)"
cp "$SETTINGS" "$BACKUP"

SS_USER="$SS_USER" SS_PASS="$SS_PASS" SETTINGS="$SETTINGS" python3 - <<'PY'
import os, re, sys
from xml.sax.saxutils import escape

path = os.environ['SETTINGS']
vals = {
    'ScraperUsernameScreenScraper': os.environ['SS_USER'],
    'ScraperPasswordScreenScraper': os.environ['SS_PASS'],
}
s = open(path).read()
for key, val in vals.items():
    pat = re.compile(r'(<string name="%s" value=")[^"]*(")' % key)
    if not pat.search(s):
        sys.exit('key %s not present in settings file' % key)
    s = pat.sub(lambda m: m.group(1) + escape(val, {'"': '&quot;'}) + m.group(2), s)

# Credentials are pointless unless ES-DE is told to use the account.
s = re.sub(r'(<bool name="ScraperUseAccountScreenScraper" value=")[^"]*(")', r'\1true\2', s)
open(path, 'w').write(s)

from xml.sax.saxutils import unescape
written = open(path).read()
for key, val in vals.items():
    got = unescape(re.search(r'<string name="%s" value="([^"]*)"' % key, written).group(1),
                   {'&quot;': '"'})
    print('%s: %d characters written%s' % (key, len(got), '' if got == val else '  *** MISMATCH ***'))
PY

echo "backup: $BACKUP"
echo "Done. Credentials are in $SETTINGS (plaintext, not mirrored to the public repo)."
