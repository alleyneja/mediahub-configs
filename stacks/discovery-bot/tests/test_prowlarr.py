import requests

from bot.albumpick import Pick
from bot.picker import Draft, annotate_notes
from bot.prowlarr import Prowlarr, NOTE
from tests.test_clients import FakeResp, FakeSession


class RaisingSession:
    def __init__(self, exc):
        self.exc = exc

    def request(self, *a, **kw):
        raise self.exc


def prowlarr(resp):
    s = FakeSession(resp)
    return Prowlarr("http://prowlarr:9696/", "k", session=s), s


def test_has_release_true_on_results():
    p, s = prowlarr(FakeResp(200, [{"title": "x"}]))
    assert p.has_release("Artist", "Album") is True
    method, url, kw = s.calls[0]
    assert method == "GET" and url == "http://prowlarr:9696/api/v1/search"
    assert kw["params"]["query"] == "Artist Album" and kw["params"]["categories"] == 3000
    assert kw["headers"]["X-Api-Key"] == "k" and kw["timeout"] == 20


def test_has_release_false_on_empty():
    p, _ = prowlarr(FakeResp(200, []))
    assert p.has_release("A", "B") is False


def test_has_release_none_on_error():
    assert prowlarr(FakeResp(500, {}))[0].has_release("A", "B") is None
    assert prowlarr(FakeResp(200, {"not": "a list"}))[0].has_release("A", "B") is None
    for exc in (requests.Timeout(), requests.ConnectionError()):
        p = Prowlarr("http://x", "k", session=RaisingSession(exc))
        assert p.has_release("A", "B") is None


class FakeProwlarr:
    def __init__(self, results):
        self.results = results

    def has_release(self, artist, album):
        r = self.results[album]
        if r == "boom":
            raise RuntimeError
        return r


def draft(title):
    return Draft({"id": "m", "name": "Art"}, Pick("mbid", title, 2000, "r"))


def test_note_added_only_when_false():
    drafts = [draft("T"), draft("F"), draft("N")]
    out = annotate_notes(drafts, FakeProwlarr({"T": True, "F": False, "N": None}))
    assert out is drafts and [d.pick.title for d in out] == ["T", "F", "N"]
    assert [d.note for d in out] == [None, NOTE, None]


def test_annotate_never_raises_or_removes():
    drafts = [draft("X")]
    assert annotate_notes(drafts, FakeProwlarr({"X": "boom"}))[0].note is None
    assert annotate_notes(drafts, None)[0].note is None
