# Discovery bot (Discord music approval)

Stack: `stacks/discovery-bot/`. Design: `docs/superpowers/specs/2026-11-06-music-discovery-approval-design.md`.
Album-choice signals: `docs/discovery-album-signals.md`.

## How it works

- Every Saturday 10:00 (`TZ`, America/Chicago) the bot picks 2 artists from
  Aurral's discover recommendations, chooses one album each, and posts a card
  (cover, year, "because you listen to ...") with Approve / Reject buttons to
  `#media-alerts`. If it was down at 10:00 it catches up within 3 days.
- Approve: adds the artist in Lidarr if needed, monitors exactly that album,
  unmonitors the artist's other albums, runs a search, and edits the card to
  "approved". Reject: stores a `rejected` row and sends feedback to Aurral;
  rejected artists are never picked again. Artists suggested in the last 60
  days are skipped.
- Pending cards expire after 14 days. Approved albums with no track files after
  7 days get one follow-up note on the card. Availability is checked against
  Prowlarr when picking.
- State is a SQLite file (`/data/discovery.db`, named volume
  `discovery-bot-data`). Buttons are persistent views re-registered at start,
  so cards posted before a restart still work.
- Runs as non-root (uid 10001) with `network_mode: host` so it can reach
  Aurral :3001, Lidarr :8686, Prowlarr :9696 on localhost. Memory limit 256m.

## Env (`stacks/discovery-bot/discovery-bot.env`, gitignored)

Template: `discovery-bot.env.example`. Keys: `DISCORD_TOKEN` (the Homelab Ops
bot token from `scripts/discord-bot.env`), `DISCORD_CHANNEL_ID`
(`#media-alerts`), `AURRAL_URL`, `AURRAL_API_KEY`, `LIDARR_URL`,
`LIDARR_API_KEY`, `PROWLARR_URL`, `PROWLARR_API_KEY`, `DB_PATH`, `TZ`.
Key sources: Aurral's key is in its DB
(`settings` table, `integrations` JSON, `general.apiKey`; same value the
`/api/auth` key endpoint returns); Lidarr/Prowlarr keys are `<ApiKey>` in
`/srv/docker/{lidarr,prowlarr}/config.xml`. Never commit or paste these.

## Run / rerun

```
cd ~/mediahub-configs/stacks/discovery-bot
docker compose up -d --build        # start / redeploy
docker compose logs -f discovery-bot # shows next scheduled run
docker compose run --rm discovery-bot python -m bot.main --post-now   # post 2 cards immediately, then exit
docker compose run --rm --no-deps --entrypoint "" \
  -v "$PWD/tests:/app/tests:ro" -v "$PWD/pytest.ini:/app/pytest.ini:ro" \
  discovery-bot python -m pytest -q                                  # tests (not baked into the image)
```

Note `--post-now` while the service is running opens a second Discord session
with the same token and writes the same DB, and the running service registers
button handlers only at startup. Cards posted by a separate `--post-now` get no
handler until the service restarts (buttons show "This interaction failed").
Stop the service first, or restart it afterwards.

## Rollback

`docker compose down` (the service is additive; nothing else depends on it).
Albums already approved stay monitored in Lidarr; undo those in Lidarr by hand.
To wipe bot state: `docker volume rm discovery-bot_discovery-bot-data`.
Cards already in Discord keep dead buttons once the bot is gone.

## First live test checklist (not yet done; run when ready)

1. `--post-now` before starting the service. Expect 2 cards in `#media-alerts`
   with sensible albums: correct year, cover art, "because you listen to".
2. Click Approve on one: in Lidarr exactly that album is monitored for the
   artist, a search ran, the card edits to approved. Click Reject on the other:
   a `rejected` row exists (`sqlite3` on the volume's `discovery.db`),
   `GET /api/discover/feedback` on Aurral lists it, and a fresh `--post-now`
   does not pick that artist.
3. Restart check: post, `docker compose restart discovery-bot`, then click
   Approve on a card posted before the restart; it should still work.
4. `docker compose up -d`, confirm the log shows the next scheduled run. Add a
   Kuma push monitor only if `docs/log-monitoring.md` shows that is the fleet
   pattern.
5. After the first real Approve, check the artist's other albums in Lidarr
   (see limits below).

## Known limits

- Rating coverage in Aurral is uneven, so roughly 20-30% of recommendations are
  skipped as unusable; a week can post fewer than 2 cards (a short alert is sent to the channel, or a "paused" notice when 4+ cards are pending).
- The Approve/Reject click path has not been exercised live yet (unit tests
  only, 98 passing); do the checklist above.
- Lidarr may not know a just-requested artist yet when the extra-album
  unmonitor runs, so extras can stay monitored. Verify after the first approve
  and unmonitor by hand if needed.
- Whether Reject feedback changes Aurral's own UI is unverified; the bot's own
  DB guarantees the artist is not suggested again.
