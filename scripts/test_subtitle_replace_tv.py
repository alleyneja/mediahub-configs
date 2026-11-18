#!/usr/bin/env python3
"""Tests for the pure parts of subtitle-replace-tv.py: rule tv-v1.1 and the per-show guard."""
import importlib.util, os
spec = importlib.util.spec_from_file_location("tv", os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitle-replace-tv.py"))
tv = importlib.util.module_from_spec(spec); spec.loader.exec_module(tv)


def rec(offs, cues=40, dur=1400, last=1390):
    return {"dur": dur, "last_cue_end": last, "windows": [None if o is None else {"offset": o, "cues": cues, "score": 1.0, "at_min": 1} for o in offs]}


def test_pass_and_outlier():
    assert tv.judge_tv(rec([0.1, -0.2, 0.3, 0.0, 0.1, -0.1]))[0] == "PASS"
    assert tv.judge_tv(rec([-1.3, -0.2, 0.6, 0.6, -0.1, -0.1]))[0] == "PASS"       # Suits S05E02: one stray window
    assert tv.judge_tv(rec([0.0, -0.1, -0.1, 0.0, -0.5, 39.0]))[0] == "PASS"       # HxH S02E42: one wild window


def test_fail_cases():
    assert tv.judge_tv(rec([-47.9, -6.7, 4.8, 23.7, -8.1, 52.6]))[0] == "FAIL"     # Suits S02E16 scattered
    assert tv.judge_tv(rec([5.0, 5.2, 4.9, 5.1, 5.0, 5.1]))[0] == "FAIL"           # plain 5 s shift
    assert tv.judge_tv(rec([-0.9, -0.9, -0.9, -0.9, -0.9, -0.9]))[0] == "FAIL"     # 0.9 s shift is over the 0.75 s limit
    assert tv.judge_tv(rec([0.0] * 6), overlap=0.14)[0] == "FAIL"                  # merged tracks (Suits S06E15)
    assert tv.judge_tv(rec([0.0] * 6, last=600))[0] == "FAIL"                      # truncated
    assert tv.judge_tv(rec([0.0, 0.0, 0.0, None, 0.0, 0.0]))[0] == "FAIL"          # a window without cues
    assert tv.judge_tv(rec([0.0] * 3 + [60.0, 60.5, 61.0]))[0] == "FAIL"           # 3 windows agree on a big shift


def test_unverifiable():
    assert tv.judge_tv(rec([0.1, 0.0, 0.1, 0.0, 50.0, -70.0]))[0] == "UNVERIFIABLE"


def test_overlap_share():
    clean = "1\n00:00:01,000 --> 00:00:02,000\na\n\n2\n00:00:03,000 --> 00:00:04,000\nb\n"
    assert tv.overlap_share(clean) == 0
    dup = clean + "\n3\n00:00:03,500 --> 00:00:05,000\nc\n"
    assert tv.overlap_share(dup) > 0.3


def test_guard_per_show():
    d = {"shows": ["Suits"], "files": ["Suits - S01E01.mkv"], "episodes": 1, "all": 1}
    assert "this show" in tv.guard_decision(d, "Suits", "Suits - S04E02.mkv")
    assert tv.guard_decision(d, "Hunter x Hunter (2011)", "x.mkv") is None
    assert tv.guard_decision({"shows": ["Hunter x Hunter"], "files": ["a"], "episodes": 1, "all": 1}, "Hunter x Hunter (2011)", "b")
    assert tv.guard_decision({"shows": [], "files": [], "episodes": 0, "all": 2}, "Suits", "s.mkv") is None     # movies / live TV only
    assert "could not be identified" in tv.guard_decision({"shows": [""], "files": [], "episodes": 1, "all": 1}, "Suits", "s.mkv")


if __name__ == "__main__":
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
