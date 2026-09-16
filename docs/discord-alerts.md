# Discord alert routing

**Status: reorganized 2026-09-15.** Previously every notifier below posted to
a single webhook/channel named `joyboy-alerts`, left over from when that
channel was created for the Joyboy trading-engine watchdog. Joyboy has since
been retired (see below), and the channel had become a hodgepodge of
unrelated alerts (outages, resource warnings, media-library notices). This
doc describes the split.

## Structure

Homelab Discord server, guild ID `1522633876550324345`, under one category:

**Alerts** (category ID `1549633458899390464`)

| Channel | Channel ID | Source | Purpose |
|---|---|---|---|
| `#service-outages` | `1549633459868401704` | Uptime Kuma | Monitor up/down for all ~39 monitored services |
| `#resource-alerts` | `1549633460971511938` | Netdata | CPU/mem/swap/disk threshold alarms |
| `#media-alerts` | `1549633462351175730` | arr-stack `notify-audio-mismatch.sh` | Sonarr/Radarr imports where the actual embedded audio language doesn't match what was grabbed |
| `#home-automation` | `1549633463231979715` | *(none yet)* | Reserved for future home-automation notifications — intentionally unwired |

## Where each webhook secret actually lives

None of the real webhook URLs are in this repo (it's public). Where to find
or rotate each one:

- **Uptime Kuma** → in-app, not a file. Its single Discord notification
  config (`notification.id = 1` in `kuma.db`) is applied to every monitor
  (`isDefault` + `applyExisting`), so there's only one place to edit. The
  Kuma UI has no working edit path for this without breaking the
  apply-to-all behavior; edit the DB directly instead:
  ```
  docker stop uptime-kuma
  sudo sqlite3 /srv/docker/visibility/uptime-kuma/kuma.db \
    "UPDATE notification SET config = '<json with new discordWebhookUrl>' WHERE id = 1;"
  docker start uptime-kuma
  ```
- **Netdata** → `/srv/docker/visibility/netdata/config/health_alarm_notify.conf`,
  `DISCORD_WEBHOOK_URL=`. This file lives on the host only — it isn't even
  gitignored, it's just never been copied into the repo, because it holds
  the webhook in plaintext. Restart the `netdata` container after editing.
- **arr-stack** → `stacks/arr-stack/discord.env`, `DISCORD_WEBHOOK_URL=`.
  Gitignored (`*.env`). Mounted read-only into both the `radarr` and
  `sonarr` containers at `/config/discord.env`; `notify-audio-mismatch.sh`
  sources it at run time, so no container restart is needed after editing.
  See `discord.env.example` for the expected format.

## The bot

A Discord Application + bot ("Homelab Ops") was created 2026-09-15 to
create/manage channels and webhooks via the API. It holds `Manage Channels`
and `Manage Webhooks` on the Homelab server only. **The bot token is stored
in Vaultwarden, not here** — add/rotate it there if it's ever needed again;
never paste it into this repo.

To add a webhook to a new channel later (e.g. wiring up
`#home-automation`), with the bot token loaded as `$DISCORD_BOT_TOKEN`:

```
curl -X POST "https://discord.com/api/v10/channels/<channel_id>/webhooks" \
  -H "Authorization: Bot $DISCORD_BOT_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"<Webhook Name>"}'
```

The response's `id` and `token` fields combine into the webhook URL:
`https://discord.com/api/webhooks/<id>/<token>`.

## Retired: joyboy-watchdog

`joyboy-watchdog.sh` (cron, every minute, `/home/jay/joyboy-watchdog.sh`)
posted Joyboy engine up/down alerts to the shared webhook. Removed
2026-09-15 — the Joyboy project is no longer active enough to justify it.
Removed: the cron entry (`cron/jay-crontab`), the script, its config
(`~/.config/joyboy-watchdog.env`), and its state file. The `joyboy-alerts`
Discord channel itself was left in place for Jay to archive/delete once the
new channels are confirmed working.
