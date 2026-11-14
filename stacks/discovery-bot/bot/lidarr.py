from dataclasses import dataclass
from typing import Optional

import requests

TIMEOUT = 60


class LidarrError(Exception):
    pass


@dataclass(frozen=True)
class Album:
    id: int
    mbid: str
    title: str
    year: Optional[int]
    monitored: bool
    track_file_count: int


def _album(d) -> Album:
    rd = d.get("releaseDate") or ""
    year = int(rd[:4]) if rd[:4].isdigit() else None
    return Album(
        id=d["id"],
        mbid=d.get("foreignAlbumId", ""),
        title=d.get("title", ""),
        year=year,
        monitored=bool(d.get("monitored")),
        track_file_count=int((d.get("statistics") or {}).get("trackFileCount", 0)),
    )


class Lidarr:
    def __init__(self, base_url, key, session=None):
        self.base = base_url.rstrip("/") + "/api/v1"
        self._key = key
        self.session = session or requests.Session()

    def _call(self, method, path, **kw):
        try:
            resp = self.session.request(
                method, self.base + path,
                headers={"X-Api-Key": self._key}, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise LidarrError(f"{method} {path} failed: {type(e).__name__}") from None
        if resp.status_code not in (200, 201, 202):
            raise LidarrError(f"{method} {path} returned {resp.status_code}")
        return resp

    def _call_json(self, method, path, **kw):
        try:
            return self._call(method, path, **kw).json()
        except ValueError:
            raise LidarrError(f"{method} {path} returned a non-JSON body") from None

    def monitored_albums(self, artist_mbid):
        """All Lidarr albums for the artist (each flagged with monitored state)."""
        artists = self._call_json("GET", "/artist")
        match = next((a for a in artists
                      if a.get("foreignArtistId", "").lower() == artist_mbid.lower()), None)
        if not match:
            return []
        albums = self._call_json("GET", "/album", params={"artistId": match["id"]})
        return [_album(a) for a in albums]

    def unmonitor(self, album_id):
        self._call("PUT", "/album/monitor", json={"albumIds": [album_id], "monitored": False})

    def album_by_mbid(self, mbid):
        albums = self._call_json("GET", "/album", params={"foreignAlbumId": mbid})
        return _album(albums[0]) if albums else None
