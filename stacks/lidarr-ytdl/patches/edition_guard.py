"""Edition guard for the scheduler (#80 follow-up, Nov 23 2026).

MusicBrainz often lists a deluxe / anniversary / "collection" re-release as its own
release group next to the standard album. Lidarr then shows both as separate albums,
the scheduler fills the one Jay does not own, and the files land in a second folder.
When MusicBrainz later merges the two, Lidarr drops the extra album and the downloaded
files are stranded: untracked, duplicating tracks Jay already has, invisible in Lidarr.

`owned_sibling()` answers: is there another album by this artist with the same title
once edition words are stripped, that is already mostly owned? If so the scheduler
skips the album and says so. It is a pure function (no I/O) so it can be unit-tested
on the host: stacks/lidarr-ytdl/patches/test_edition_guard.py.

It is deliberately title-based and conservative: "Greatest Hits II" and "Daydream"
vs "Daydream (30th Anniversary)" are told apart, and anything it skips can still be
queued by hand from the web UI.
"""
import re

_EDITION_WORDS = re.compile(
    r"\b(deluxe|super|anniversary|expanded|remaster(?:ed)?|edition|collection|bonus|"
    r"version|special|tour|platinum|limited|complete|extended|original|stereo|mono|"
    r"explicit|clean|club|\d+(?:st|nd|rd|th))\b"
)


def base_title(title):
    """Lowercase title with bracketed text, edition words and punctuation removed."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", (title or "").lower())
    t = _EDITION_WORDS.sub(" ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def owned_fraction(album):
    s = album.get("statistics") or {}
    total = int(s.get("trackCount") or 0)
    return (int(s.get("trackFileCount") or 0) / total) if total else 0.0


def owned_sibling(album, artist_albums, min_owned=0.5):
    """The owned album `album` duplicates, or None.

    album: a Lidarr album dict (needs id, title). artist_albums: all of that artist's
    Lidarr albums. A sibling counts when its base title equals ours (and is not empty)
    and at least `min_owned` of its tracks have files.
    """
    mine = base_title(album.get("title"))
    if not mine:
        return None
    best = None
    for other in artist_albums:
        if other.get("id") == album.get("id"):
            continue
        if base_title(other.get("title")) != mine:
            continue
        frac = owned_fraction(other)
        if frac >= min_owned and (best is None or frac > owned_fraction(best)):
            best = other
    return best
