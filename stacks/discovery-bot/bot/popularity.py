"""Album popularity from data Aurral already returns (MusicBrainz ratings)."""

PRIOR_MEAN = 6.0   # on MusicBrainz' 0-10 scale as surfaced by Aurral
PRIOR_WEIGHT = 5   # pseudo-votes: a lone 10/10 vote must not beat a well-rated album


def ratings_popularity(release_groups):
    """Map release-group mbid -> shrunken rating, for groups that have a rating."""
    out = {}
    for r in release_groups or []:
        rating = r.get("rating")
        if not r.get("id") or not isinstance(rating, dict):
            continue
        try:
            count, value = int(rating.get("count") or 0), float(rating["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if count <= 0:
            continue
        out[r["id"]] = (count * value + PRIOR_WEIGHT * PRIOR_MEAN) / (count + PRIOR_WEIGHT)
    return out
