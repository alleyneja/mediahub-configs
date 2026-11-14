"""Entry point: weekly cards, expiry, follow-ups. Logic lives in small functions that
take callables so tests use fakes; only main()/_build_client touch Discord."""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

import discord

from .actions import expire_stale, followup_missing
from .aurral import Aurral, AurralError
from .cards import CardView, Deps, build_embed, register_persistent_views
from .config import load_settings
from .lidarr import Lidarr, LidarrError
from .prowlarr import Prowlarr
from .picker import MAX_PENDING, build_week
from .schedule import latest_run, should_catch_up
from .state import Store

log = logging.getLogger(__name__)
LOOP_SECONDS = 15 * 60


def _utcnow():
    return datetime.now(timezone.utc)


def _reason(e):
    """Short alert text. Only our own sanitized client errors expose their message."""
    if isinstance(e, (AurralError, LidarrError)):
        return f"{type(e).__name__}: {str(e)[:200]}"
    return type(e).__name__


async def run_weekly(store, aurral, now, tz, post, alert, attempted, prowlarr=None):
    """Post this week's cards when due. One attempt per scheduled slot (attempted set)."""
    if not should_catch_up(now, store.last_post_at(), tz):
        return
    slot = latest_run(now, tz)
    if slot in attempted:
        return
    attempted.add(slot)
    paused = await asyncio.to_thread(store.pending_count) >= MAX_PENDING
    try:
        drafts = await asyncio.to_thread(build_week, aurral, store, now, 2, prowlarr=prowlarr)
    except AurralError as e:
        await alert(f"Weekly music suggestions skipped: {_reason(e)}")
        return
    if paused:
        await alert(f"Weekly music suggestions paused: {MAX_PENDING}+ cards still pending.")
    elif len(drafts) < 2:
        await alert(f"Only {len(drafts)} music suggestion card(s) this week "
                    "(not enough usable recommendations).")
    for d in drafts:
        try:
            await post(d, now)
        except Exception as e:  # one failed card must not drop the others
            log.exception("posting card failed")
            await alert(f"Could not post a suggestion card ({_reason(e)})")


async def run_expiry(store, now, edit):
    for sug in await asyncio.to_thread(expire_stale, store, now):
        try:
            await edit(sug, "Expired")
        except Exception as e:  # message deleted etc.; status is already stored
            log.warning("could not edit expired card %s: %s", sug.id, type(e).__name__)


async def run_followups(store, lidarr, now, edit):
    for sug in await asyncio.to_thread(followup_missing, store, lidarr, now):
        try:
            await edit(sug, "Approved, not found yet")
        except Exception as e:
            log.warning("could not edit follow-up card %s: %s", sug.id, type(e).__name__)


async def loop_pass(stages, alert, last_alerts):
    """Run each (name, coroutine function); alert on failure (deduped until it recovers)."""
    for name, fn in stages:
        try:
            await fn()
            last_alerts.pop(name, None)
        except Exception as e:
            log.exception("%s pass failed", name)
            text = f"discovery-bot: {name} failed ({_reason(e)})"
            if last_alerts.get(name) != text:
                last_alerts[name] = text
                try:
                    await alert(text)
                except Exception:
                    log.exception("could not post alert")


# ---- Discord glue (not unit tested) ----

async def _post_card(channel, store, deps, client, draft, now):
    pick = draft.pick
    msg = await channel.send(embed=build_embed(draft))
    sid = store.add_suggestion(draft.rec.get("id", ""), draft.rec.get("name", ""),
                               pick.mbid, pick.title, pick.year, msg.id, now)
    view = CardView(sid, deps)
    if client is not None:
        client.add_view(view, message_id=msg.id)
    await msg.edit(view=view)


async def _edit_card(channel, sug, text):
    msg = await channel.fetch_message(int(sug.message_id))
    embed = msg.embeds[0] if msg.embeds else discord.Embed()
    embed.set_footer(text=text)
    await msg.edit(embed=embed, view=None)


async def _post_now(client, settings, store, aurral, deps, prowlarr=None):
    channel = await client.fetch_channel(settings.channel_id)
    try:
        drafts = await asyncio.to_thread(build_week, aurral, store, _utcnow(), 2, prowlarr=prowlarr)
    except AurralError as e:
        await channel.send(f"Music suggestions skipped: {_reason(e)}")
        return 1
    for d in drafts:
        await _post_card(channel, store, deps, client, d, _utcnow())
    print(f"posted {len(drafts)} card(s)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-now", action="store_true", help="post 2 cards now and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(os.environ)
    store = Store(settings.db_path)
    aurral = Aurral(settings.aurral_url, settings.aurral_key)
    lidarr = Lidarr(settings.lidarr_url, settings.lidarr_key)
    prowlarr = Prowlarr(settings.prowlarr_url, settings.prowlarr_key)
    deps = Deps(store, aurral, lidarr)
    client = discord.Client(intents=discord.Intents.default())

    if args.post_now:
        async def once():
            async with client:
                await client.login(settings.discord_token)
                return await _post_now(client, settings, store, aurral, deps, prowlarr)
        return asyncio.run(once())

    state = {"started": False}
    attempted, last_alerts = set(), {}

    async def background():
        channel = None
        while True:
            try:
                if channel is None:
                    channel = await client.fetch_channel(settings.channel_id)
                    stages = _make_stages(channel)
                await loop_pass(stages, channel.send, last_alerts)
            except Exception as e:  # setup failed; retry next tick instead of dying
                log.error("background setup failed: %s", type(e).__name__)
                channel = None
            await asyncio.sleep(LOOP_SECONDS)

    def _make_stages(channel):
        async def post(d, now):
            await _post_card(channel, store, deps, client, d, now)

        async def edit(sug, text):
            await _edit_card(channel, sug, text)

        return [
            ("weekly post", lambda: run_weekly(store, aurral, _utcnow(), settings.tz,
                                               post, channel.send, attempted, prowlarr)),
            ("expiry", lambda: run_expiry(store, _utcnow(), edit)),
            ("follow-up", lambda: run_followups(store, lidarr, _utcnow(), edit)),
        ]

    def _task_done(task):
        if not task.cancelled() and task.exception() is not None:
            log.error("background task died: %s", type(task.exception()).__name__)

    @client.event
    async def on_ready():
        if state["started"]:
            return
        state["started"] = True
        register_persistent_views(client, store, aurral, lidarr)
        client._loop_task = asyncio.create_task(background())
        client._loop_task.add_done_callback(_task_done)

    client.run(settings.discord_token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
