import json
from pathlib import Path

from bot.albumpick import Pick, pick_album
from bot.popularity import ratings_popularity

FIX = Path(__file__).parent / "fixtures"
GILL_1990 = "0a32879d-47b5-3df2-8a69-1574b0c2954f"
GILL_1983 = "1c0402f1-8bda-4508-b9da-0f8f118cf2aa"


def rg(i, title, date, primary="Album", secondary=(), rating=None):
    return {"id": i, "title": title, "first-release-date": date,
            "primary-type": primary, "secondary-types": list(secondary), "rating": rating}


def test_excludes_compilations_live_and_singles():
    groups = [
        rg("comp", "Greatest Hits", "2000-01-01", secondary=["Compilation"]),
        rg("live", "Live!", "2001-01-01", secondary=["Live"]),
        rg("single", "Hit", "2002-01-01", primary="Single"),
        rg("ep", "Small", "2003-01-01", primary="EP"),
        rg("studio", "Real Album", "1999-05-05"),
    ]
    pop = {"comp": 99, "live": 98, "single": 97, "ep": 96, "studio": 1}
    pick = pick_album(groups, pop)
    assert pick.mbid == "studio"
    assert pick.year == 1999


def test_highest_popularity_wins():
    groups = [rg("a", "A", "1990-01-01"), rg("b", "B", "1995-01-01"), rg("c", "C", "2000-01-01")]
    pick = pick_album(groups, {"a": 3.0, "b": 8.0, "c": 5.0})
    assert pick.mbid == "b" and pick.title == "B"
    assert isinstance(pick, Pick) and pick.reason


def test_johnny_gill_resolves_to_1990():
    groups = json.loads((FIX / "artist_johnny_gill.json").read_text())["release-groups"]
    pick = pick_album(groups, {GILL_1990: 8.5, GILL_1983: 6.0})
    assert pick.mbid == GILL_1990
    assert pick.year == 1990
    assert pick.title == "Johnny Gill"


def test_same_title_albums_distinguished_by_mbid_and_year():
    groups = [rg("old", "Self Titled", "1983-04-17"), rg("new", "Self Titled", "1990-04-17")]
    assert pick_album(groups, {"old": 2.0, "new": 7.0}).mbid == "new"
    assert pick_album(groups, {"old": 7.0, "new": 2.0}).year == 1983


def test_no_confident_pick_returns_none():
    groups = [rg("a", "A", "1990-01-01"), rg("b", "B", "1995-01-01")]
    assert pick_album(groups, {}) is None
    assert pick_album(groups, {"a": 5.0, "b": 5.0}) is None  # tie is not confident
    assert pick_album([], {"a": 5.0}) is None


def test_single_eligible_album_is_picked_without_popularity():
    groups = [rg("only", "Only", "2010-02-02"), rg("s", "Single", "2010-01-01", primary="Single")]
    pick = pick_album(groups, {})
    assert pick.mbid == "only"


def test_unknown_year_is_none():
    pick = pick_album([rg("x", "X", "0001-01-01")], {})
    assert pick.year is None


def test_ratings_popularity_shrinks_low_vote_counts():
    groups = [
        rg("few", "Few", "2000-01-01", rating={"count": 1, "value": 10}),
        rg("many", "Many", "2001-01-01", rating={"count": 40, "value": 8}),
        rg("none", "None", "2002-01-01"),
    ]
    pop = ratings_popularity(groups)
    assert pop["many"] > pop["few"]
    assert "none" not in pop
