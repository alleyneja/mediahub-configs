"""Scheduler for automatic missing album detection and queueing.

Periodically checks Lidarr for missing albums and optionally adds
them to the download queue.
"""

import logging
import time

import schedule

import models
from config import load_config, retry_cooldown_seconds
from edition_guard import owned_sibling
from lidarr import get_missing_albums, lidarr_request
from notifications import send_notifications
from processing import download_process, queue_lock

logger = logging.getLogger(__name__)

# Albums last reported with auto-download off: nothing gets attempted, so
# nothing cools down, and the same list would be re-notified every cycle.
_last_reported_ids = set()

# Albums already announced as skipped duplicate editions (one notice each per
# process lifetime; the skip itself is re-evaluated every tick).
_edition_skips_reported = set()


def _skip_owned_editions(albums, config):
    """Drop albums that are another edition of an album Jay already mostly owns.

    See edition_guard.py. Fails open: any Lidarr error keeps the album queued.
    Disable with scheduler_skip_owned_editions = false in the app config.
    """
    if not config.get("scheduler_skip_owned_editions", True):
        return albums
    by_artist, kept, skipped = {}, [], []
    for album in albums:
        artist_id = (album.get("artist") or {}).get("id")
        if artist_id is None:
            kept.append(album)
            continue
        if artist_id not in by_artist:
            resp = lidarr_request("album", params={"artistId": artist_id})
            by_artist[artist_id] = resp if isinstance(resp, list) else None
        siblings = by_artist[artist_id]
        match = owned_sibling(album, siblings) if siblings else None
        if match:
            skipped.append((album, match))
        else:
            kept.append(album)
    new = [(a, m) for a, m in skipped if a["id"] not in _edition_skips_reported]
    for album, match in skipped:
        logger.info(
            "Scheduler: skipping %r, another edition of %r which is already"
            " owned", album.get("title"), match.get("title"),
        )
    if new:
        _edition_skips_reported.update(a["id"] for a, _ in new)
        names = "\n".join(
            f"- {a.get('artist', {}).get('artistName', '')} - {a.get('title', '')}"
            f" (same as owned: {m.get('title', '')})" for a, m in new[:15]
        )
        send_notifications(
            f"Scheduler: skipped {len(new)} duplicate edition(s)",
            log_type="album_error",
            embed_data={
                "title": "Scheduler: duplicate edition skipped",
                "description": (
                    "Another edition of an album that is already mostly owned;"
                    " not queued so the library does not get a second copy."
                    " Queue by hand if you do want it:\n" + names
                ),
                "color": 0x95A5A6,
            },
        )
    return kept


def scheduled_check():
    """Check Lidarr for new missing albums and optionally queue them."""
    try:
        _scheduled_check()
    except Exception:
        logger.exception("Scheduled missing-album check failed")


def _scheduled_check():
    with queue_lock:
        is_active = download_process["active"]
    if is_active:
        return
    config = load_config()
    albums = get_missing_albums()

    if not albums:
        return

    interval_minutes = _interval_minutes(config)
    lookback_seconds = interval_minutes * 60 * 2
    now = time.time()
    recent_history_ids = models.get_history_album_ids_since(
        now - lookback_seconds
    )

    # Skip albums that were already attempted recently (success OR
    # failure) so the scheduler doesn't keep retrying the same failing
    # album every cycle before working through the rest of the library.
    cooldown = retry_cooldown_seconds(config)
    attempted_ids = set()
    if cooldown:
        attempted_ids = models.get_attempted_album_ids_since(now - cooldown)

    with queue_lock:
        current_download_id = download_process.get("album_id")

    queued_ids = {row["album_id"] for row in models.get_queue()}

    new_albums = [
        album
        for album in albums
        if album["id"] not in queued_ids
        and album["id"] not in recent_history_ids
        and album["id"] not in attempted_ids
        and album["id"] != current_download_id
        and album.get("missingTrackCount", 0) > 0
    ]

    if not new_albums:
        return

    new_albums = _skip_owned_editions(new_albums, config)
    if not new_albums:
        return

    # One pass first: while any album has never been attempted, only those
    # are queued. Albums that are still partly missing after an attempt wait
    # until the whole library has had its first try, then retry as before.
    ever_attempted = models.get_attempted_album_ids_since(0)
    fresh_albums = [a for a in new_albums if a["id"] not in ever_attempted]
    if fresh_albums:
        logger.info(
            f"Scheduler: {len(fresh_albums)} never-attempted albums remain;"
            f" deferring {len(new_albums) - len(fresh_albums)} retries"
        )
        new_albums = fresh_albums

    # Retry cap: an album attempted scheduler_max_attempts times and still
    # missing tracks (hopeless: no YouTube match, or a loop) stops being
    # retried. One notice the first tick it would otherwise have been retried.
    # 0 = unlimited. Set to 3 by default.
    max_attempts = int(config.get("scheduler_max_attempts", 3))
    if max_attempts > 0:
        conn = models.db.get_db()
        stats = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT album_id, COUNT(*), MAX(timestamp) FROM download_logs"
                " WHERE type='download_started' GROUP BY album_id"
            )
        }
        capped, kept = [], []
        for album in new_albums:
            count, last = stats.get(album["id"], (0, 0))
            if count >= max_attempts:
                capped.append((album, last))
            else:
                kept.append(album)
        if capped:
            window = interval_minutes * 60
            fresh = [
                a for a, last in capped
                if cooldown + 0 <= now - last < cooldown + window
            ]
            logger.info(
                f"Scheduler: skipping {len(capped)} albums that hit the"
                f" {max_attempts}-attempt retry cap"
            )
            if fresh:
                names = "\n".join(
                    f"- {a.get('artist', {}).get('artistName', '')} -"
                    f" {a.get('title', '')}" for a in fresh[:15]
                )
                send_notifications(
                    f"Scheduler: {len(fresh)} albums gave up after"
                    f" {max_attempts} attempts",
                    log_type="album_error",
                    embed_data={
                        "title": "Scheduler: retry cap reached",
                        "description": (
                            f"Still missing tracks after {max_attempts}"
                            " attempts; no longer retried automatically"
                            " (queue by hand to retry):\n" + names
                        ),
                        "color": 0xE67E22,
                    },
                )
        new_albums = kept
        if not new_albums:
            return

    max_albums = int(config.get("scheduler_max_albums", 0))
    if max_albums > 0:
        new_albums = new_albums[:max_albums]

    if config.get("scheduler_auto_download", True):
        logger.info(
            f"Scheduler: Found {len(new_albums)} new missing albums,"
            " adding to queue..."
        )
        send_notifications(
            f"Scheduler: Adding {len(new_albums)} new missing"
            " albums to queue...",
            log_type="download_started",
            embed_data={
                "title": "Scheduler",
                "description": (
                    f"Adding {len(new_albums)} new missing"
                    " albums to queue"
                ),
                "color": 0x3498DB,
            },
        )
        for album in new_albums:
            models.enqueue_album(album["id"])
    else:
        global _last_reported_ids
        ids = {a["id"] for a in new_albums}
        fresh = len(ids - _last_reported_ids)
        _last_reported_ids = ids
        if not fresh:
            logger.debug(
                f"Scheduler: {len(ids)} missing albums, none new"
                " (Auto-Download disabled)"
            )
            return
        logger.info(
            f"Scheduler: Found {fresh} missing albums"
            " (Auto-Download disabled)"
        )
        send_notifications(
            f"Scheduler: Found {fresh} missing albums"
            " (Auto-DL Disabled)",
            log_type="download_started",
            embed_data={
                "title": "Scheduler",
                "description": (
                    f"Found {fresh} missing albums"
                    " (Auto-DL Disabled)"
                ),
                "color": 0xE67E22,
            },
        )


def run_scheduler():
    """Run the schedule loop forever, checking every 10 seconds."""
    while True:
        try:
            schedule.run_pending()
        except Exception:
            logger.exception("Scheduler job failed; will retry next cycle")
        time.sleep(10)


def next_run_timestamp():
    """Unix time of the next scheduled check, or None when no job is set."""
    if not schedule.get_jobs():
        return None
    return schedule.next_run().timestamp()


def _interval_minutes(config):
    # schedule.every(0) never returns (its next-run loop can't advance), and
    # a config import or SCHEDULER_INTERVAL=0 can carry any int here.
    return max(1, int(config.get("scheduler_interval", 60)))


def setup_scheduler():
    """Configure the scheduler based on current config settings."""
    config = load_config()
    schedule.clear()
    if config.get("scheduler_enabled"):
        schedule.every(_interval_minutes(config)).minutes.do(scheduled_check)
