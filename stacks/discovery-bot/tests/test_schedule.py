import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.actions import expire_stale, followup_missing
from bot.aurral import AurralError
from bot.lidarr import Album, LidarrError
from bot.main import run_expiry, run_followups, run_weekly, loop_pass
from bot.picker import Draft
from bot.albumpick import Pick
from bot.schedule import next_run, should_catch_up
from bot.state import Store

CHI = ZoneInfo("America/Chicago")
UTC = timezone.utc


def local(y, m, d, h=10, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=CHI)


def test_next_run_is_saturday_10_chicago():
    now = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)  # Friday
    r = next_run(now, "America/Chicago")
    assert r.tzinfo is not None
    assert r == local(2026, 11, 7)
    l = r.astimezone(CHI)
    assert (l.weekday(), l.hour, l.minute) == (5, 10, 0)


def test_next_run_is_strictly_future():
    sat_10 = local(2026, 11, 7).astimezone(UTC)
    assert next_run(sat_10, "America/Chicago") == local(2026, 11, 14)
    assert next_run(sat_10 - timedelta(seconds=1), "America/Chicago") == sat_10


def test_next_run_across_dst_change():
    # US fall back 2026-11-01 (Sun): Sat 10:00 is CDT (UTC-5) before, CST (UTC-6) after
    before = next_run(datetime(2026, 10, 28, 12, tzinfo=UTC), "America/Chicago")
    after = next_run(datetime(2026, 11, 2, 12, tzinfo=UTC), "America/Chicago")
    assert before.astimezone(UTC) == datetime(2026, 10, 31, 15, tzinfo=UTC)
    assert after.astimezone(UTC) == datetime(2026, 11, 7, 16, tzinfo=UTC)
    # spring forward 2027-03-14
    b = next_run(datetime(2027, 3, 10, 12, tzinfo=UTC), "America/Chicago")
    a = next_run(datetime(2027, 3, 15, 12, tzinfo=UTC), "America/Chicago")
    assert b.astimezone(UTC) == datetime(2027, 3, 13, 16, tzinfo=UTC)
    assert a.astimezone(UTC) == datetime(2027, 3, 20, 15, tzinfo=UTC)


def test_catch_up_within_three_days():
    now = local(2026, 11, 9, 9).astimezone(UTC)  # Monday 09:00, missed Saturday
    last = local(2026, 10, 31, 10, 1).astimezone(UTC)
    assert should_catch_up(now, last, "America/Chicago") is True
    assert should_catch_up(now, None, "America/Chicago") is True


def test_no_catch_up_after_three_days():
    now = local(2026, 11, 10, 10, 1).astimezone(UTC)  # Tue, > 3 days after Sat 10:00
    last = local(2026, 10, 31, 10, 1).astimezone(UTC)
    assert should_catch_up(now, last, "America/Chicago") is False


def test_no_catch_up_before_first_run_of_week():
    now = local(2026, 11, 7, 9, 59).astimezone(UTC)
    # latest slot is Oct 31 10:00, already posted at 10:01
    assert should_catch_up(now, local(2026, 10, 31, 10, 1).astimezone(UTC), "America/Chicago") is False


def test_no_double_post_same_week():
    now = local(2026, 11, 7, 12).astimezone(UTC)
    assert should_catch_up(now, local(2026, 11, 7, 10, 2).astimezone(UTC), "America/Chicago") is False
    assert should_catch_up(now, local(2026, 11, 7, 10, 0).astimezone(UTC), "America/Chicago") is False
    assert should_catch_up(now, local(2026, 10, 31, 10, 2).astimezone(UTC), "America/Chicago") is True


NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def add(store, n, posted):
    return store.add_suggestion(f"a{n}", f"A{n}", f"al{n}", f"Al{n}", 2000, f"m{n}", posted)


def test_expire_stale_after_14_days(store):
    old = add(store, 1, NOW - timedelta(days=15))
    fresh = add(store, 2, NOW - timedelta(days=13))
    approved = add(store, 3, NOW - timedelta(days=20))
    store.claim(approved, NOW - timedelta(days=19))
    out = expire_stale(store, NOW)
    assert [s.id for s in out] == [old]
    assert store.get(old).status == "expired"
    assert store.get(fresh).status == "pending"
    assert store.get(approved).status == "approved"
    assert expire_stale(store, NOW) == []


class FL:
    def __init__(self, albums=None, fail=False):
        self.albums, self.fail = albums or {}, fail

    def monitored_albums(self, artist_mbid):
        if self.fail:
            raise LidarrError("down")
        return self.albums.get(artist_mbid, [])


def alb(mbid, files):
    return Album(1, mbid, "t", 2000, True, files)


def test_followup_only_when_no_track_files(store):
    a = add(store, 1, NOW - timedelta(days=9))
    b = add(store, 2, NOW - timedelta(days=9))
    c = add(store, 3, NOW - timedelta(days=9))
    for i in (a, b, c):
        store.claim(i, NOW - timedelta(days=8))
    young = add(store, 4, NOW - timedelta(days=3))
    store.claim(young, NOW - timedelta(days=2))
    lid = FL({"a1": [alb("al1", 0)], "a2": [alb("AL2", 5)], "a3": [alb("other", 0)]})
    out = followup_missing(store, lid, NOW)
    assert sorted(s.id for s in out) == [a, c]
    assert store.get(a).noted and store.get(b).noted and not store.get(young).noted
    assert followup_missing(store, lid, NOW) == []


def test_followup_lidarr_error_leaves_unnoted(store):
    a = add(store, 1, NOW - timedelta(days=9))
    store.claim(a, NOW - timedelta(days=8))
    assert followup_missing(store, FL(fail=True), NOW) == []
    assert not store.get(a).noted


# ---- main loop pieces ----

def run(c):
    return asyncio.run(c)


def draft(n):
    return Draft({"id": f"a{n}", "name": f"A{n}"}, Pick(f"al{n}", f"Al{n}", 2000, "r"))


def test_run_weekly_posts_once_per_slot(store, monkeypatch):
    posted, alerts, attempted = [], [], set()
    monkeypatch.setattr("bot.main.build_week", lambda a, s, n, k=2, **kw: [draft(1), draft(2)])

    async def post(d, now):
        posted.append(d.rec["id"])
        add(store, len(posted), now)

    now = local(2026, 11, 7, 10, 5).astimezone(UTC)
    async def alert(m): alerts.append(m)
    kw = dict(store=store, aurral=None, tz="America/Chicago", post=post,
              alert=alert, attempted=attempted)
    run(run_weekly(now=now, **kw))
    run(run_weekly(now=now + timedelta(minutes=15), **kw))
    assert posted == ["a1", "a2"] and alerts == []


def test_run_weekly_not_due(store, monkeypatch):
    monkeypatch.setattr("bot.main.build_week", lambda *a, **k: pytest.fail("called"))
    now = local(2026, 11, 11, 12).astimezone(UTC)  # Wednesday, far from Saturday
    run(run_weekly(store=store, aurral=None, now=now, tz="America/Chicago",
                   post=None, alert=None, attempted=set()))


def test_run_weekly_aurral_error_alerts_once_no_cards(store, monkeypatch):
    def boom(*a, **k):
        raise AurralError("Aurral returned no recommendations")
    monkeypatch.setattr("bot.main.build_week", boom)
    alerts, posted, attempted = [], [], set()

    async def alert(m): alerts.append(m)
    async def post(d, now): posted.append(d)
    now = local(2026, 11, 7, 10, 5).astimezone(UTC)
    for i in range(3):
        run(run_weekly(store=store, aurral=None, now=now + timedelta(minutes=15 * i),
                       tz="America/Chicago", post=post, alert=alert, attempted=attempted))
    assert len(alerts) == 1 and "no recommendations" in alerts[0] and posted == []


def test_run_expiry_edits_each(store):
    old = add(store, 1, NOW - timedelta(days=15))
    edits = []

    async def edit(sug, text): edits.append((sug.id, text))
    run(run_expiry(store, NOW, edit))
    assert edits == [(old, "Expired")]


def test_run_expiry_edit_failure_does_not_abort(store):
    add(store, 1, NOW - timedelta(days=15))
    add(store, 2, NOW - timedelta(days=16))
    seen = []

    async def edit(sug, text):
        seen.append(sug.id)
        raise RuntimeError("message deleted")
    run(run_expiry(store, NOW, edit))
    assert len(seen) == 2


def test_run_followups_edits(store):
    a = add(store, 1, NOW - timedelta(days=9))
    store.claim(a, NOW - timedelta(days=8))
    edits = []

    async def edit(sug, text): edits.append((sug.id, text))
    run(run_followups(store, FL({"a1": [alb("al1", 0)]}), NOW, edit))
    assert edits == [(a, "Approved, not found yet")]


def test_loop_pass_alerts_and_continues_and_dedupes():
    alerts, ran = [], []
    last = {}

    async def alert(m): alerts.append(m)

    async def bad():
        raise RuntimeError("secret-token-xyz")

    async def good():
        ran.append(1)

    for _ in range(2):
        run(loop_pass([("weekly", bad), ("expiry", good)], alert, last))
    assert ran == [1, 1]
    assert len(alerts) == 1 and "weekly" in alerts[0] and "secret-token-xyz" not in alerts[0]


def test_run_weekly_one_failed_post_does_not_drop_other(store, monkeypatch):
    monkeypatch.setattr("bot.main.build_week", lambda a, s, n, k=2, **kw: [draft(1), draft(2)])
    posted, alerts = [], []

    async def post(d, now):
        if d.rec["id"] == "a1":
            raise RuntimeError("discord 503 token-xyz")
        posted.append(d.rec["id"])

    async def alert(m): alerts.append(m)
    now = local(2026, 11, 7, 10, 5).astimezone(UTC)
    run(run_weekly(store=store, aurral=None, now=now, tz="America/Chicago",
                   post=post, alert=alert, attempted=set()))
    assert posted == ["a2"]
    assert len(alerts) == 1 and "token-xyz" not in alerts[0]


def _weekly(store, monkeypatch, drafts):
    monkeypatch.setattr("bot.main.build_week", lambda a, s, n, k=2, **kw: drafts)
    alerts = []

    async def alert(m): alerts.append(m)
    async def post(d, now): pass
    now = local(2026, 11, 7, 10, 5).astimezone(UTC)
    run(run_weekly(store=store, aurral=None, now=now, tz="America/Chicago",
                   post=post, alert=alert, attempted=set()))
    return alerts


def test_run_weekly_alerts_when_fewer_than_two_drafts(store, monkeypatch):
    alerts = _weekly(store, monkeypatch, [draft(1)])
    assert len(alerts) == 1 and "1" in alerts[0]
    assert len(_weekly(store, monkeypatch, [])) == 1


def test_run_weekly_paused_at_four_pending_gives_paused_notice(store, monkeypatch):
    for n in range(1, 5):
        add(store, n, NOW - timedelta(days=30))
    alerts = _weekly(store, monkeypatch, [])
    assert len(alerts) == 1 and "paused" in alerts[0].lower()


def test_run_weekly_two_drafts_no_alert(store, monkeypatch):
    assert _weekly(store, monkeypatch, [draft(1), draft(2)]) == []
