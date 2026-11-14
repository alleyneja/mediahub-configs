import json
from pathlib import Path

import pytest

from bot.aurral import Aurral, AurralError
from bot.lidarr import Lidarr, LidarrError

FIX = Path(__file__).parent / "fixtures"
JG_MBID = "7dc35846-c93a-4261-b665-ae750e3afdfb"
JG_ALBUM = "0a32879d-47b5-3df2-8a69-1574b0c2954f"


class FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


class FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.resp


def fixture(name):
    return json.loads((FIX / name).read_text())


def aurral(resp):
    s = FakeSession(resp)
    return Aurral("http://a:3001/", "k", session=s), s


def test_recommendations_parses_fixture():
    a, s = aurral(FakeResp(200, fixture("discover.json")))
    recs = a.recommendations()
    assert len(recs) == 5
    assert recs[0]["id"] and recs[0]["name"] and "score" in recs[0]
    method, url, kw = s.calls[0]
    assert (method, url) == ("GET", "http://a:3001/api/discover")
    assert kw["headers"]["x-api-key"] == "k"
    assert kw["timeout"] == 60


def test_recommendations_empty_raises():
    a, _ = aurral(FakeResp(200, {"recommendations": []}))
    with pytest.raises(AurralError):
        a.recommendations()


def test_recommendations_non_200_raises():
    a, _ = aurral(FakeResp(500, {"error": "boom"}))
    with pytest.raises(AurralError):
        a.recommendations()


def test_discography_returns_release_groups():
    a, s = aurral(FakeResp(200, fixture("artist_johnny_gill.json")))
    rgs = a.discography(JG_MBID)
    assert any(r["id"] == JG_ALBUM for r in rgs)
    assert s.calls[0][1].endswith(f"/api/artists/{JG_MBID}")


def test_request_album_sends_managedby_lidarr_and_triggersearch():
    a, s = aurral(FakeResp(200, {"success": True}))
    a.request_album(JG_MBID, "Johnny Gill", JG_ALBUM, "Johnny Gill")
    method, url, kw = s.calls[0]
    assert (method, url) == ("POST", "http://a:3001/api/library/albums/request")
    assert kw["json"] == {
        "albumMbid": JG_ALBUM,
        "albumName": "Johnny Gill",
        "artistMbid": JG_MBID,
        "artistName": "Johnny Gill",
        "triggerSearch": True,
        "managedBy": "lidarr",
    }


def test_request_album_error_raises_with_message():
    a, _ = aurral(FakeResp(400, {"error": "x", "message": "Album already requested"}))
    with pytest.raises(AurralError, match="Album already requested"):
        a.request_album(JG_MBID, "Johnny Gill", JG_ALBUM, "Johnny Gill")


LIB = [
    {"mbid": JG_MBID, "foreignArtistId": JG_MBID, "name": "Johnny Gill", "artistName": "Johnny Gill"},
    {"mbid": None, "foreignArtistId": "name:artist:02", "name": "O2", "artistName": "O2"},
]


def test_library_names_are_lowercased():
    a, _ = aurral(FakeResp(200, LIB))
    assert a.library_artist_names() == {"johnny gill", "o2"}


def test_library_mbids_skip_name_placeholders():
    a, _ = aurral(FakeResp(200, LIB))
    assert a.library_artist_mbids() == {JG_MBID}


def test_send_feedback_body():
    a, s = aurral(FakeResp(200, {"success": True}))
    a.send_feedback(JG_MBID, "Johnny Gill", "less_like_this", ["rnb"], ["Seed Artist A"])
    method, url, kw = s.calls[0]
    assert (method, url) == ("POST", "http://a:3001/api/discover/feedback")
    assert kw["json"] == {
        "artistId": JG_MBID,
        "artistName": "Johnny Gill",
        "action": "less_like_this",
        "tagContext": ["rnb"],
        "seedContext": ["Seed Artist A"],
    }


def test_send_feedback_rejects_unknown_action():
    a, _ = aurral(FakeResp(200, {}))
    with pytest.raises(ValueError):
        a.send_feedback(JG_MBID, "x", "nuke", [], [])


class MultiSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses[(method, url.split("/api/v1")[1])]


def test_lidarr_monitored_albums_reports_track_file_count():
    albums = [
        {"id": 7, "title": "Johnny Gill", "foreignAlbumId": JG_ALBUM, "monitored": True,
         "releaseDate": "1990-04-17T00:00:00Z", "statistics": {"trackFileCount": 12}},
        {"id": 8, "title": "Provocative", "foreignAlbumId": "x", "monitored": False,
         "releaseDate": None, "statistics": {"trackFileCount": 0}},
    ]
    s = MultiSession({
        ("GET", "/artist"): FakeResp(200, [{"id": 3, "foreignArtistId": JG_MBID}]),
        ("GET", "/album"): FakeResp(200, albums),
    })
    got = Lidarr("http://l:8686", "k", session=s).monitored_albums(JG_MBID)
    assert [(g.mbid, g.title, g.year, g.monitored, g.track_file_count) for g in got] == [
        (JG_ALBUM, "Johnny Gill", 1990, True, 12),
        ("x", "Provocative", None, False, 0),
    ]
    assert s.calls[1][2]["params"] == {"artistId": 3}
    assert s.calls[0][2]["headers"]["X-Api-Key"] == "k"


def test_lidarr_unknown_artist_returns_empty():
    s = MultiSession({("GET", "/artist"): FakeResp(200, [])})
    assert Lidarr("http://l:8686", "k", session=s).monitored_albums(JG_MBID) == []


def test_lidarr_unmonitor_puts_album_monitor():
    s = MultiSession({("PUT", "/album/monitor"): FakeResp(202, {})})
    Lidarr("http://l:8686", "k", session=s).unmonitor(7)
    method, url, kw = s.calls[0]
    assert (method, kw["json"]) == ("PUT", {"albumIds": [7], "monitored": False})


def test_lidarr_album_by_mbid():
    s = MultiSession({("GET", "/album"): FakeResp(200, [
        {"id": 7, "title": "Johnny Gill", "foreignAlbumId": JG_ALBUM, "monitored": True,
         "releaseDate": "1990-04-17T00:00:00Z", "statistics": {"trackFileCount": 0}}])})
    a = Lidarr("http://l:8686", "k", session=s).album_by_mbid(JG_ALBUM)
    assert a.id == 7 and a.year == 1990
    assert s.calls[0][2]["params"] == {"foreignAlbumId": JG_ALBUM}


class BadJson(FakeResp):
    def json(self):
        raise ValueError("<html>secret body</html>")


def test_aurral_bad_json_on_request_raises_aurral_error():
    a, _ = aurral(BadJson(200))
    with pytest.raises(AurralError) as e:
        a.request_album("m", "n", "am", "at")
    assert "secret" not in str(e.value)


def test_lidarr_bad_json_raises_lidarr_error():
    s = FakeSession(BadJson(200))
    with pytest.raises(LidarrError) as e:
        Lidarr("http://l:8686", "k", session=s).monitored_albums("m")
    assert "secret" not in str(e.value)
