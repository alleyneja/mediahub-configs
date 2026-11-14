import re

import requests

TIMEOUT = 60
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_ACTIONS = {"more_like_this", "less_like_this", "block_artist"}


class AurralError(Exception):
    pass


class Aurral:
    def __init__(self, base_url, key, session=None):
        self.base = base_url.rstrip("/")
        self._key = key
        self.session = session or requests.Session()

    def _call(self, method, path, **kw):
        try:
            return self.session.request(
                method, self.base + path,
                headers={"x-api-key": self._key}, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise AurralError(f"{method} {path} failed: {type(e).__name__}") from None

    @staticmethod
    def _message(resp):
        try:
            body = resp.json()
            if isinstance(body, dict):
                return str(body.get("message") or body.get("error") or "")
        except ValueError:
            pass
        return ""

    def _get(self, path):
        resp = self._call("GET", path)
        if resp.status_code != 200:
            raise AurralError(f"GET {path} returned {resp.status_code} {self._message(resp)}".strip())
        return self._json(resp, path)

    @staticmethod
    def _json(resp, path):
        try:
            return resp.json()
        except ValueError:
            raise AurralError(f"{path} returned a non-JSON body") from None

    def recommendations(self):
        recs = self._get("/api/discover").get("recommendations") or []
        if not recs:
            raise AurralError("Aurral returned no recommendations")
        return recs

    def discography(self, mbid):
        return self._get(f"/api/artists/{mbid}").get("release-groups") or []

    def _library(self):
        return self._get("/api/library/artists")

    def library_artist_mbids(self):
        out = set()
        for a in self._library():
            for v in (a.get("mbid"), a.get("foreignArtistId")):
                if v and _UUID.match(v):
                    out.add(v.lower())
        return out

    def library_artist_names(self):
        out = set()
        for a in self._library():
            for v in (a.get("name"), a.get("artistName")):
                if v:
                    out.add(v.lower())
        return out

    def request_album(self, artist_mbid, artist_name, album_mbid, album_title):
        resp = self._call("POST", "/api/library/albums/request", json={
            "albumMbid": album_mbid,
            "albumName": album_title,
            "artistMbid": artist_mbid,
            "artistName": artist_name,
            "triggerSearch": True,
            "managedBy": "lidarr",
        })
        if resp.status_code not in (200, 201, 202):
            raise AurralError(self._message(resp) or f"request failed ({resp.status_code})")
        return self._json(resp, "request")

    def send_feedback(self, artist_mbid, artist_name, action, tags, seeds):
        if action not in _ACTIONS:
            raise ValueError(f"invalid feedback action: {action}")
        resp = self._call("POST", "/api/discover/feedback", json={
            "artistId": artist_mbid,
            "artistName": artist_name,
            "action": action,
            "tagContext": list(tags or []),
            "seedContext": list(seeds or []),
        })
        if resp.status_code != 200:
            raise AurralError(self._message(resp) or f"feedback failed ({resp.status_code})")
        return self._json(resp, "feedback")
