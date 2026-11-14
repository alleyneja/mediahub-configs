"""Informational indexer check. Never raises; None means 'unknown'."""
import logging

import requests

log = logging.getLogger(__name__)
TIMEOUT = 20
AUDIO_CATEGORY = 3000
NOTE = "No usenet/torrent release found yet — may rely on the YouTube fallback"


class Prowlarr:
    def __init__(self, base_url, key, session=None):
        self.base = base_url.rstrip("/") + "/api/v1"
        self._key = key
        self.session = session or requests.Session()

    def has_release(self, artist, album):
        try:
            resp = self.session.request(
                "GET", self.base + "/search",
                params={"query": f"{artist} {album}", "type": "search",
                        "categories": AUDIO_CATEGORY},
                headers={"X-Api-Key": self._key}, timeout=TIMEOUT)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, list):
                return None
            return len(data) > 0
        except Exception as e:  # informational only: never let it break a week's cards
            log.warning("prowlarr check failed: %s", type(e).__name__)
            return None
