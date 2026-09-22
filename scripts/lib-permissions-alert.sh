#!/bin/bash
# Shared Discord webhook poster for the permissions-monitor family of scripts.
# Usage: source this file, then call: send_discord_alert "message text"
send_discord_alert() {
    local message="$1"
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local env_file="$script_dir/permissions-alerts.env"

    if [ ! -f "$env_file" ]; then
        echo "send_discord_alert: missing $env_file" >&2
        return 1
    fi
    # shellcheck disable=SC1090
    source "$env_file"
    if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
        echo "send_discord_alert: DISCORD_WEBHOOK_URL not set in $env_file" >&2
        return 1
    fi

    local payload
    payload=$(python3 -c '
import json, sys
print(json.dumps({"content": sys.argv[1][:1900]}))
' "$message")

    curl -sf --max-time 10 -X POST -H "Content-Type: application/json" -d "$payload" "$DISCORD_WEBHOOK_URL" >/dev/null
}
