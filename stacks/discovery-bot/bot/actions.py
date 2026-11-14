"""Approve/reject logic, kept free of Discord objects so it tests with fakes."""
import asyncio
import contextlib
import logging
from collections import namedtuple
from datetime import timedelta

from .aurral import AurralError
from .lidarr import LidarrError

log = logging.getLogger(__name__)

Result = namedtuple("Result", "ok message")


def _outcome(sug):
    """Stored outcome for a card that was already answered (no side effects)."""
    if sug is None:
        return Result(False, "Unknown suggestion.")
    if sug.status == "approved":
        return Result(True, "Already approved.")
    if sug.status == "rejected":
        return Result(True, "Already rejected.")
    if sug.status == "expired":
        return Result(False, "This suggestion expired.")
    return Result(False, "Suggestion is pending.")


COULD_NOT_VERIFY = " (Could not verify Lidarr monitoring.)"
_locks = {}  # sug_id -> [asyncio.Lock, users]


async def approve(sug_id, store, aurral, lidarr, now):
    """Serialized per suggestion: a concurrent click waits for the in-flight result."""
    async with _locked(sug_id):
        return await _approve(sug_id, store, aurral, lidarr, now)


@contextlib.asynccontextmanager
async def _locked(sug_id):
    entry = _locks.setdefault(sug_id, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            _locks.pop(sug_id, None)


async def _approve(sug_id, store, aurral, lidarr, now):
    sug = store.get(sug_id)
    if sug is None:
        return _outcome(None)
    if not store.claim(sug_id, now):
        return _outcome(store.get(sug_id))
    try:
        await asyncio.to_thread(aurral.request_album, sug.artist_mbid, sug.artist_name,
                                sug.album_mbid, sug.album_title)
    except AurralError as e:
        store.reopen(sug_id)
        return Result(False, f"Request failed: {e}")
    except Exception as e:  # never leave a claimed row with an unedited card
        log.exception("unexpected error requesting suggestion %s", sug_id)
        store.reopen(sug_id)
        return Result(False, f"Request failed: unexpected error ({type(e).__name__})")
    note = ""
    try:
        albums = await asyncio.to_thread(lidarr.monitored_albums, sug.artist_mbid)
        if not any(a.mbid.lower() == sug.album_mbid.lower() for a in albums):
            # artist unknown to Lidarr yet, or the MBID was merged/redirected:
            # nothing was verified, so unmonitor nothing
            log.warning("lidarr has no album %s for suggestion %s", sug.album_mbid, sug_id)
            note = COULD_NOT_VERIFY
        else:
            extras = [a for a in albums
                      if a.monitored and a.mbid.lower() != sug.album_mbid.lower()]
            for a in extras:
                await asyncio.to_thread(lidarr.unmonitor, a.id)
            if extras:
                note = f" Unmonitored {len(extras)} extra album(s)."
    except Exception as e:  # approval stands; just say it was not verified
        log.warning("lidarr verify failed for suggestion %s: %s", sug_id, type(e).__name__)
        note = COULD_NOT_VERIFY
    return Result(True, f"Approved, search started.{note}")


def needs_alert(message):
    """Approve notes that should be visible to the channel, not just the clicker."""
    return "Unmonitored" in message or "Could not verify" in message


async def reject(sug_id, store, aurral, now):
    async with _locked(sug_id):  # wait out an in-flight approve that may reopen
        return await _reject(sug_id, store, aurral, now)


async def _reject(sug_id, store, aurral, now):
    sug = store.get(sug_id)
    if sug is None:
        return _outcome(None)
    if not store.set_status(sug_id, "rejected", now):
        return _outcome(store.get(sug_id))
    note = ""
    try:
        await asyncio.to_thread(aurral.send_feedback, sug.artist_mbid, sug.artist_name,
                                "less_like_this", [], [])
    except AurralError as e:
        log.warning("aurral feedback failed for suggestion %s: %s", sug_id, e)
        note = " (Aurral feedback not sent.)"
    return Result(True, f"Rejected.{note}")


EXPIRE_DAYS = 14


def expire_stale(store, now, days=EXPIRE_DAYS):
    """Mark pending suggestions older than `days` as expired; return the ones changed."""
    cutoff = now - timedelta(days=days)
    out = []
    for sid in store.pending_ids():
        sug = store.get(sid)
        if sug.posted_at <= cutoff and store.set_status(sid, "expired", now):
            out.append(store.get(sid))
    return out


def followup_missing(store, lidarr, now):
    """Approved >= 7 days ago with no track files yet. Each suggestion is checked once:
    it is marked noted whether or not the album arrived, so the follow-up posts once."""
    out = []
    for sug in store.approved_without_note(now, days=7):
        try:
            albums = lidarr.monitored_albums(sug.artist_mbid)
        except LidarrError as e:
            log.warning("lidarr follow-up check failed for suggestion %s: %s", sug.id, e)
            continue
        match = next((a for a in albums if a.mbid.lower() == sug.album_mbid.lower()), None)
        store.mark_noted(sug.id)
        if match is None or match.track_file_count == 0:
            out.append(store.get(sug.id))
    return out
