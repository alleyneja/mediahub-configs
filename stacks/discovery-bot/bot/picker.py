"""Weekly candidate selection: rank recommendations, enforce variety, draft picks."""
import logging
from dataclasses import dataclass
from typing import Optional

from .albumpick import Pick, pick_album
from .aurral import AurralError
from .popularity import ratings_popularity

log = logging.getLogger(__name__)
MAX_PENDING = 4


@dataclass
class Draft:
    rec: dict
    pick: Pick
    note: Optional[str] = None


def _first(rec, key):
    vals = rec.get(key) or []
    return vals[0] if vals else None


def _ranked(recs, library_mbids, library_names, known_mbids):
    """Eligible recs, best score first. MBID match first; name match as a fallback."""
    keep = []
    for r in recs:
        mbid = (r.get("id") or "").lower()
        name = (r.get("name") or "").lower()
        if mbid and (mbid in library_mbids or mbid in known_mbids):
            continue
        if name and name in library_names:
            continue
        keep.append(r)
    return sorted(keep, key=lambda r: r.get("score") or 0, reverse=True)


def _varied(r, seeds, tags):
    seed, tag = _first(r, "sourceArtists"), _first(r, "matchedTags")
    return not ((seed is not None and seed in seeds) or (tag is not None and tag in tags))


def select_candidates(recs, library_mbids, library_names, known_mbids, n=2):
    chosen, seeds, tags = [], set(), set()
    for r in _ranked(recs, library_mbids, library_names, known_mbids):
        if len(chosen) >= n:
            break
        if not _varied(r, seeds, tags):
            continue
        chosen.append(r)
        seeds.add(_first(r, "sourceArtists"))
        tags.add(_first(r, "matchedTags"))
    return chosen


def annotate_notes(drafts, prowlarr):
    """Attach an informational note when no indexer release exists. Never drops/reorders."""
    if prowlarr is None:
        return drafts
    from .prowlarr import NOTE
    for d in drafts:
        try:
            found = prowlarr.has_release(d.rec.get("name", ""), d.pick.title)
        except Exception:
            found = None
        if found is False:
            d.note = NOTE
    return drafts


def build_week(aurral, store, now, n=2, prowlarr=None):
    if store.pending_count() >= MAX_PENDING:
        return []
    recs = aurral.recommendations()  # AurralError propagates: caller alerts and skips the week
    ranked = _ranked(recs, aurral.library_artist_mbids(), aurral.library_artist_names(),
                     store.known_artist_mbids(now))
    drafts, seeds, tags = [], set(), set()
    for r in ranked:
        if len(drafts) >= n:
            break
        if not r.get("id"):
            log.warning("skipping recommendation without an id: %s", r.get("name"))
            continue
        if not _varied(r, seeds, tags):
            continue
        try:
            rgs = aurral.discography(r["id"])
        except AurralError as e:
            log.warning("discography failed for %s: %s", r.get("name"), e)
            continue
        pick = pick_album(rgs, ratings_popularity(rgs))
        if pick is None:
            continue
        drafts.append(Draft(r, pick))
        seeds.add(_first(r, "sourceArtists"))
        tags.add(_first(r, "matchedTags"))
    return annotate_notes(drafts, prowlarr)
