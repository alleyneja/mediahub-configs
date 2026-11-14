"""Choose the one best-known studio album for a recommended artist.

Pure: no network. Popularity scores are supplied by the caller (see popularity.py).
"""
from collections import namedtuple

Pick = namedtuple("Pick", "mbid title year reason")


def _year(date):
    try:
        year = int(str(date or "")[:4])
    except ValueError:
        return None
    return year if year >= 1000 else None


def _eligible(release_groups):
    return [r for r in release_groups
            if r.get("id") and r.get("primary-type") == "Album" and not r.get("secondary-types")]


def _pick(r, reason):
    return Pick(r["id"], r.get("title") or "", _year(r.get("first-release-date")), reason)


def pick_album(release_groups, popularity):
    """Return a Pick, or None when there is no confident choice (skip the artist)."""
    albums = _eligible(release_groups or [])
    if not albums:
        return None
    scored = sorted(((popularity.get(r["id"], 0.0), r) for r in albums),
                    key=lambda t: t[0], reverse=True)
    top_score, top = scored[0]
    if top_score > 0:
        if len(scored) > 1 and scored[1][0] == top_score:
            return None
        return _pick(top, "highest rated studio album")
    if len(albums) == 1:
        return _pick(albums[0], "only studio album")
    return None
