import asyncio
from datetime import datetime, timezone

import pytest

from bot.actions import approve, reject
from bot.albumpick import Pick
from bot.aurral import AurralError
from bot.cards import CardView, build_embed
from bot.lidarr import Album, LidarrError
from bot.picker import Draft
from bot.state import Store

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class FakeAurral:
    def __init__(self, fail_request=False, fail_feedback=False):
        self.requests, self.feedback = [], []
        self.fail_request, self.fail_feedback = fail_request, fail_feedback

    def request_album(self, artist_mbid, artist_name, album_mbid, album_title):
        self.requests.append((artist_mbid, album_mbid))
        if self.fail_request:
            raise AurralError("boom")
        return {}

    def send_feedback(self, artist_mbid, artist_name, action, tags, seeds):
        self.feedback.append((artist_mbid, action))
        if self.fail_feedback:
            raise AurralError("nope")
        return {}


class FakeLidarr:
    def __init__(self, albums=(), fail=False):
        self.albums, self.fail, self.unmonitored = list(albums), fail, []

    def monitored_albums(self, artist_mbid):
        if self.fail:
            raise LidarrError("down")
        return self.albums

    def unmonitor(self, album_id):
        self.unmonitored.append(album_id)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "s.db"))


@pytest.fixture
def sid(store):
    return store.add_suggestion("artist-1", "Artist 1", "album-1", "Album 1", 2020, "m1", NOW)


def run(c):
    return asyncio.run(c)


def test_approve_requests_album_then_marks_approved(store, sid):
    a = FakeAurral()
    ok, msg = run(approve(sid, store, a, FakeLidarr(), NOW))
    assert ok and a.requests == [("artist-1", "album-1")]
    assert store.get(sid).status == "approved"


def test_approve_failure_leaves_pending_and_returns_error(store, sid):
    ok, msg = run(approve(sid, store, FakeAurral(fail_request=True), FakeLidarr(), NOW))
    assert not ok and "boom" in msg
    assert store.get(sid).status == "pending"


def test_approve_twice_requests_once(store, sid):
    a = FakeAurral()
    run(approve(sid, store, a, FakeLidarr(), NOW))
    ok, msg = run(approve(sid, store, a, FakeLidarr(), NOW))
    assert ok and len(a.requests) == 1


def test_approve_unmonitors_any_extra_monitored_album(store, sid):
    l = FakeLidarr([Album(1, "album-1", "A", 2020, True, 0),
                    Album(2, "other", "B", 2019, True, 0),
                    Album(3, "third", "C", 2018, False, 0)])
    ok, msg = run(approve(sid, store, FakeAurral(), l, NOW))
    assert ok and l.unmonitored == [2]


def test_lidarr_error_does_not_revert_approval(store, sid):
    ok, msg = run(approve(sid, store, FakeAurral(), FakeLidarr(fail=True), NOW))
    assert ok and store.get(sid).status == "approved"


def test_reject_records_rejected_and_sends_less_like_this(store, sid):
    a = FakeAurral()
    ok, msg = run(reject(sid, store, a, NOW))
    assert ok and store.get(sid).status == "rejected"
    assert a.feedback == [("artist-1", "less_like_this")]


def test_reject_still_records_when_aurral_feedback_fails(store, sid):
    ok, msg = run(reject(sid, store, FakeAurral(fail_feedback=True), NOW))
    assert ok and store.get(sid).status == "rejected"


def test_click_on_expired_card_reports_expired(store, sid):
    store.set_status(sid, "expired", NOW)
    a = FakeAurral()
    ok, msg = run(approve(sid, store, a, FakeLidarr(), NOW))
    assert not ok and "expired" in msg.lower() and a.requests == []
    ok, msg = run(reject(sid, store, a, NOW))
    assert not ok and a.feedback == []
    assert store.get(sid).status == "expired"


def test_second_reject_has_no_side_effects(store, sid):
    a = FakeAurral()
    run(reject(sid, store, a, NOW))
    ok, _ = run(reject(sid, store, a, NOW))
    assert ok and len(a.feedback) == 1


def test_embed_and_custom_ids():
    rec = {"id": "art", "name": "After 7", "sourceArtists": list("abcdef"),
           "matchedTags": ["t1", "t2", "t3", "t4", "t5"]}
    d = Draft(rec, Pick("rg-mbid", "Takin' My Time", 1992, "x"), note="not on usenet")
    e = build_embed(d)
    assert e.title == "After 7 — Takin' My Time (1992)"
    assert e.image.url == "https://coverartarchive.org/release-group/rg-mbid/front-250"
    text = " ".join(f.value for f in e.fields) + (e.description or "")
    assert "a, b, c, d" in text and "e" not in text.split("t1")[0].replace("Because you listen to", "")
    assert "t4" in text and "t5" not in text and "not on usenet" in text
    ids = [c.custom_id for c in CardView(7, None).children]
    assert ids == ["disc:approve:7", "disc:reject:7"]


def test_register_persistent_views_for_pending_only(store, sid):
    done = store.add_suggestion("a2", "A2", "al2", "Al2", 2001, "m2", NOW)
    store.set_status(done, "rejected", NOW)

    class C:
        views = []
        def add_view(self, v): self.views.append(v)

    from bot.cards import register_persistent_views
    c = C()
    register_persistent_views(c, store, object(), object())
    assert [v.sug_id for v in c.views] == [sid]


class SlowAurral(FakeAurral):
    def request_album(self, *a):
        import time
        time.sleep(0.05)
        return super().request_album(*a)


async def _two_clicks(store, sid, aurral):
    return await asyncio.gather(*(approve(sid, store, aurral, FakeLidarr(), NOW)
                                  for _ in range(2)))


def test_concurrent_approve_failure_leaves_pending_and_reports_failure(store, sid):
    a = SlowAurral(fail_request=True)
    r1, r2 = run(_two_clicks(store, sid, a))
    assert not r1.ok and not r2.ok  # second click never told "Already approved"
    assert store.get(sid).status == "pending"  # card keeps its buttons


def test_concurrent_approve_success_requests_once(store, sid):
    a = SlowAurral()
    r1, r2 = run(_two_clicks(store, sid, a))
    assert r1.ok and r2.ok and len(a.requests) == 1
    assert store.get(sid).status == "approved"


# ---- final review fixes ----

def test_approve_note_for_unmonitored_extras(store, sid):
    l = FakeLidarr([Album(1, "album-1", "A", 2020, True, 0), Album(2, "o", "B", 2019, True, 0)])
    ok, msg = run(approve(sid, store, FakeAurral(), l, NOW))
    assert ok and "Unmonitored 1 extra" in msg


def test_unknown_artist_in_lidarr_says_could_not_verify(store, sid):
    l = FakeLidarr([])
    ok, msg = run(approve(sid, store, FakeAurral(), l, NOW))
    assert ok and "Could not verify" in msg and l.unmonitored == []


def test_requested_mbid_absent_unmonitors_nothing(store, sid):
    l = FakeLidarr([Album(2, "o", "B", 2019, True, 0), Album(3, "p", "C", 2018, True, 0)])
    ok, msg = run(approve(sid, store, FakeAurral(), l, NOW))
    assert ok and "Could not verify" in msg and l.unmonitored == []


def test_unexpected_error_on_request_reopens(store, sid):
    class Bad(FakeAurral):
        def request_album(self, *a):
            raise ValueError("secret body text")
    ok, msg = run(approve(sid, store, Bad(), FakeLidarr(), NOW))
    assert not ok and "secret" not in msg
    assert store.get(sid).status == "pending"


def test_unexpected_error_on_verify_keeps_approval(store, sid):
    class Bad(FakeLidarr):
        def monitored_albums(self, m):
            raise ValueError("x")
    ok, msg = run(approve(sid, store, FakeAurral(), Bad(), NOW))
    assert ok and "Could not verify" in msg and store.get(sid).status == "approved"


def test_reject_during_inflight_approve_waits_for_lock(store, sid):
    import threading
    gate = threading.Event()

    class Slow(FakeAurral):
        def request_album(self, *a):
            gate.wait(5)
            raise AurralError("down")

    async def scenario():
        a = Slow()
        t1 = asyncio.create_task(approve(sid, store, a, FakeLidarr(), NOW))
        await asyncio.sleep(0.1)
        t2 = asyncio.create_task(reject(sid, store, a, NOW))
        await asyncio.sleep(0.1)
        gate.set()
        return await t1, await t2

    r1, r2 = run(scenario())
    assert not r1.ok and r2 == (True, "Rejected.")
    assert store.get(sid).status == "rejected"
