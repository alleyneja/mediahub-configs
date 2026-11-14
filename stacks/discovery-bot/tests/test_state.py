from datetime import datetime, timedelta, timezone

import pytest

from bot.config import load_settings
from bot.state import Store

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "state.db"))


def add(store, n, now=NOW):
    return store.add_suggestion(f"artist-{n}", f"Artist {n}", f"album-{n}",
                                f"Album {n}", 2020, f"msg-{n}", now)


def test_rejected_artist_always_known(store):
    sid = add(store, 1, NOW - timedelta(days=500))
    assert store.set_status(sid, "rejected", NOW - timedelta(days=500))
    assert "artist-1" in store.known_artist_mbids(NOW)


def test_expired_artist_known_only_within_60_days(store):
    sid = add(store, 1, NOW - timedelta(days=61))
    store.set_status(sid, "expired", NOW - timedelta(days=60))
    assert "artist-1" not in store.known_artist_mbids(NOW)
    sid2 = add(store, 2, NOW - timedelta(days=10))
    store.set_status(sid2, "expired", NOW - timedelta(days=9))
    assert "artist-2" in store.known_artist_mbids(NOW)


def test_pending_known_regardless_of_age(store):
    add(store, 1, NOW - timedelta(days=200))
    assert "artist-1" in store.known_artist_mbids(NOW)


def test_pending_count_ignores_answered(store):
    a, b, c = add(store, 1), add(store, 2), add(store, 3)
    store.set_status(a, "approved", NOW)
    store.set_status(b, "rejected", NOW)
    assert store.pending_count() == 1
    assert store.get(c).status == "pending"


def test_set_status_is_idempotent_and_returns_false_when_already_answered(store):
    sid = add(store, 1)
    assert store.set_status(sid, "approved", NOW) is True
    assert store.set_status(sid, "rejected", NOW) is False
    assert store.get(sid).status == "approved"


def test_claim_only_first_caller_wins(store):
    sid = add(store, 1)
    assert store.claim(sid) is True
    assert store.claim(sid) is False
    assert store.get(sid).status == "approved"


def test_claim_fails_on_rejected(store):
    sid = add(store, 1)
    store.set_status(sid, "rejected", NOW)
    assert store.claim(sid) is False


def test_reopen_reverts_approved_to_pending(store):
    sid = add(store, 1)
    store.claim(sid)
    assert store.reopen(sid) is True
    assert store.get(sid).status == "pending"
    assert store.reopen(sid) is False
    assert store.claim(sid) is True


def test_by_message_and_get_missing(store):
    sid = add(store, 1)
    assert store.by_message("msg-1").id == sid
    assert store.by_message("nope") is None
    assert store.get(999) is None


def test_message_id_unique(store):
    add(store, 1)
    with pytest.raises(Exception):
        store.add_suggestion("x", "X", "y", "Y", 2000, "msg-1", NOW)


def test_invalid_status_rejected(store):
    sid = add(store, 1)
    with pytest.raises(ValueError):
        store.set_status(sid, "bogus", NOW)


def test_approved_without_note_and_mark_noted(store):
    old = add(store, 1, NOW - timedelta(days=10))
    store.set_status(old, "approved", NOW - timedelta(days=8))
    recent = add(store, 2, NOW - timedelta(days=3))
    store.set_status(recent, "approved", NOW - timedelta(days=2))
    due = store.approved_without_note(NOW)
    assert [s.id for s in due] == [old]
    store.mark_noted(old)
    assert store.approved_without_note(NOW) == []


def test_last_post_at(store):
    assert store.last_post_at() is None
    add(store, 1, NOW - timedelta(days=2))
    add(store, 2, NOW)
    assert store.last_post_at() == NOW


def test_persists_across_reopen(tmp_path):
    p = str(tmp_path / "s.db")
    Store(p).add_suggestion("a", "A", "b", "B", 1999, "m", NOW)
    assert Store(p).pending_count() == 1


def test_load_settings_ok_and_missing():
    env = {"DISCORD_TOKEN": "t", "DISCORD_CHANNEL_ID": "123",
           "AURRAL_URL": "http://a", "AURRAL_API_KEY": "k",
           "LIDARR_URL": "http://l", "LIDARR_API_KEY": "k2",
           "PROWLARR_URL": "http://p", "PROWLARR_API_KEY": "k3"}
    s = load_settings(env)
    assert s.channel_id == 123
    assert s.db_path
    assert s.tz == "America/Chicago"
    assert load_settings({**env, "TZ": ""}).tz == "America/Chicago"
    del env["LIDARR_API_KEY"]
    with pytest.raises(RuntimeError, match="LIDARR_API_KEY"):
        load_settings(env)


def test_claim_records_answered_at_for_note_window(store):
    sid = add(store, 1, NOW - timedelta(days=9))
    assert store.claim(sid, NOW - timedelta(days=8))
    assert [s.id for s in store.approved_without_note(NOW)] == [sid]


def test_mbids_stored_lowercase_so_rejected_artist_never_returns(store):
    sid = store.add_suggestion("ABC-DEF", "A", "AL-1", "Al", 2000, "mx", NOW)
    store.set_status(sid, "rejected", NOW)
    assert store.get(sid).artist_mbid == "abc-def" and store.get(sid).album_mbid == "al-1"
    assert "abc-def" in store.known_artist_mbids(NOW)
