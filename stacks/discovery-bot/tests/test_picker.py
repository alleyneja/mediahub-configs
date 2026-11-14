from datetime import datetime, timezone

import pytest

from bot.albumpick import Pick
from bot.aurral import AurralError
from bot.picker import Draft, build_week, select_candidates

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def rec(mbid, name, score, seed="S", tag="t"):
    return {"id": mbid, "name": name, "score": score,
            "sourceArtists": [seed], "matchedTags": [tag]}


def test_drops_library_artist_by_mbid():
    recs = [rec("a", "A", 9), rec("b", "B", 5, "S2", "t2")]
    out = select_candidates(recs, {"a"}, set(), set())
    assert [r["id"] for r in out] == ["b"]


def test_drops_library_artist_by_name_case_insensitively():
    recs = [rec("a", "EarthGang", 9), rec("b", "B", 5, "S2", "t2")]
    out = select_candidates(recs, set(), {"earthgang"}, set())
    assert [r["id"] for r in out] == ["b"]


def test_drops_known_mbids():
    out = select_candidates([rec("a", "A", 9)], set(), set(), {"a"})
    assert out == []


def test_variety_rule_avoids_same_seed_and_tag():
    recs = [rec("a", "A", 9, "S1", "t1"), rec("b", "B", 8, "S1", "t2"),
            rec("c", "C", 7, "S2", "t1"), rec("d", "D", 6, "S3", "t3")]
    out = select_candidates(recs, set(), set(), set(), n=2)
    assert [r["id"] for r in out] == ["a", "d"]


class FakeAurral:
    def __init__(self, recs, discos=None, lib_mbids=(), lib_names=()):
        self._recs, self.discos = recs, discos or {}
        self.lib_mbids, self.lib_names = set(lib_mbids), set(lib_names)
        self.fetched = []

    def recommendations(self):
        if isinstance(self._recs, Exception):
            raise self._recs
        return self._recs

    def library_artist_mbids(self):
        return self.lib_mbids

    def library_artist_names(self):
        return self.lib_names

    def discography(self, mbid):
        self.fetched.append(mbid)
        d = self.discos.get(mbid, [])
        if isinstance(d, Exception):
            raise d
        return d


class FakeStore:
    def __init__(self, pending=0, known=()):
        self.pending, self.known = pending, set(known)

    def pending_count(self):
        return self.pending

    def known_artist_mbids(self, now, cooldown_days=60):
        return self.known


def album(i, rating=8.0):
    return {"id": i, "title": f"T{i}", "primary-type": "Album", "secondary-types": [],
            "first-release-date": "2000-01-01", "rating": {"count": 10, "value": rating}}


def test_pause_at_four_pending():
    a = FakeAurral([rec("a", "A", 9)], {"a": [album("x")]})
    assert build_week(a, FakeStore(pending=4), NOW) == []
    assert a.fetched == []


def test_builds_drafts():
    a = FakeAurral([rec("a", "A", 9)], {"a": [album("x")]})
    out = build_week(a, FakeStore(), NOW)
    assert len(out) == 1 and isinstance(out[0], Draft)
    assert out[0].rec["id"] == "a" and isinstance(out[0].pick, Pick)
    assert out[0].pick.mbid == "x" and out[0].note is None


def test_skips_artist_with_no_pick_and_continues():
    recs = [rec("a", "A", 9, "S1", "t1"), rec("b", "B", 8, "S2", "t2"),
            rec("c", "C", 7, "S3", "t3")]
    a = FakeAurral(recs, {"a": [], "b": [album("y")], "c": [album("z")]})
    out = build_week(a, FakeStore(), NOW, n=2)
    assert [d.rec["id"] for d in out] == ["b", "c"]


def test_discography_error_skips_artist():
    recs = [rec("a", "A", 9, "S1", "t1"), rec("b", "B", 8, "S2", "t2")]
    a = FakeAurral(recs, {"a": AurralError("boom"), "b": [album("y")]})
    out = build_week(a, FakeStore(), NOW)
    assert [d.rec["id"] for d in out] == ["b"]


def test_recommendations_error_propagates():
    with pytest.raises(AurralError):
        build_week(FakeAurral(AurralError("none")), FakeStore(), NOW)


def test_fewer_than_n_available_returns_what_exists():
    a = FakeAurral([rec("a", "A", 9)], {"a": [album("x")]})
    assert len(build_week(a, FakeStore(), NOW, n=2)) == 1


def test_build_week_applies_library_and_known_filters():
    recs = [rec("a", "A", 9, "S1", "t1"), rec("b", "B", 8, "S2", "t2"),
            rec("c", "C", 7, "S3", "t3")]
    a = FakeAurral(recs, {k: [album(k + "1")] for k in "abc"}, lib_mbids={"a"})
    out = build_week(a, FakeStore(known={"b"}), NOW)
    assert [d.rec["id"] for d in out] == ["c"]


def test_rec_without_id_is_skipped_not_fatal():
    recs = [{"name": "NoId", "score": 99, "sourceArtists": ["S0"], "matchedTags": ["t0"]},
            rec("b", "B", 8, "S2", "t2")]
    a = FakeAurral(recs, {"b": [album("y")]})
    assert [d.rec["id"] for d in build_week(a, FakeStore(), NOW)] == ["b"]
