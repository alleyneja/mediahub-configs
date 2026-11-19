import importlib.util, json, os, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("enf", os.path.join(HERE, "subtitle-enforce.py"))
enf = importlib.util.module_from_spec(spec); spec.loader.exec_module(enf)


def row(state="FAIL", reasons=("a usable window reads +2.0 s",), table="movies", ts=100, path="/m/a.en.srt", fp="1:2"):
    return {"state": state, "reasons": list(reasons), "table": table, "ts": ts, "path": path, "fp": fp}


class T(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(enf.classify(row()), "replace")
        self.assertEqual(enf.classify(row(state="PASS", reasons=[])), "ignore")
        self.assertEqual(enf.classify(row(state="UNVERIFIABLE")), "ignore")
        self.assertEqual(enf.classify(row(table="episodes")), "ignore")
        self.assertEqual(enf.classify(row(reasons=["last cue ends at 90% of the runtime"])), "review-coverage")
        # coverage plus another reason is a real FAIL
        self.assertEqual(enf.classify(row(reasons=["last cue ends at 60% of the runtime", "a usable window reads +9 s"])), "replace")

    def test_classify_tv(self):
        self.assertEqual(enf.classify_tv("FAIL", ["a usable window reads +2.0 s"]), "replace")
        self.assertEqual(enf.classify_tv("FAIL", ["only 5 of 6 windows have enough cues (missing dialogue or truncated)", "last cue ends at 88% of the runtime"]), "replace")
        self.assertEqual(enf.classify_tv("FAIL", ["last cue ends at 88% of the runtime"]), "review-coverage")
        self.assertEqual(enf.classify_tv("PASS", []), "ignore")
        self.assertEqual(enf.classify_tv("UNVERIFIABLE", ["only 4 usable windows; nothing contradicts the subtitle"]), "ignore")

    def test_plan_tv_work(self):
        rows = [{"id": 5, "eid": 1, "path": "/a.srt", "fp": "1:1"}, {"id": 9, "eid": 1, "path": "/a.srt", "fp": "2:2"},
                {"id": 7, "eid": 2, "path": "/b.srt", "fp": "3:3"}, {"id": 8, "eid": 3, "path": "/c.srt", "fp": "4:4"}]
        # newest row per path, oldest first, handled (path|fp) removed
        self.assertEqual([x["id"] for x in enf.plan_tv_work(rows, [])], [7, 8, 9])
        self.assertEqual([x["id"] for x in enf.plan_tv_work(rows, ["/b.srt|3:3"])], [8, 9])
        # a handled older fingerprint does not hide the newer file
        self.assertEqual([x["id"] for x in enf.plan_tv_work(rows, ["/a.srt|1:1"])], [7, 8, 9])
        self.assertEqual(enf.plan_tv_work(rows, ["/a.srt|2:2", "/b.srt|3:3", "/c.srt|4:4"]), [])

    def test_order_tv_backlog(self):
        items = [{"eid": 1, "show": "Alpha", "season": 1, "episode": 1}, {"eid": 2, "show": "Alpha", "season": 1, "episode": 2},
                 {"eid": 3, "show": "Beta (2011)", "season": 1, "episode": 1}, {"eid": 4, "show": "Gamma", "season": 1, "episode": 1}]
        # watched shows first, most recent first; library order kept inside a show; "(2011)" is ignored when matching
        self.assertEqual([x["eid"] for x in enf.order_tv_backlog(items, ["Gamma", "Beta"], set())], [4, 3, 1, 2])
        self.assertEqual([x["eid"] for x in enf.order_tv_backlog(items, [], set())], [1, 2, 3, 4])
        # handled episodes drop out
        self.assertEqual([x["eid"] for x in enf.order_tv_backlog(items, ["Gamma"], {"eid:4", "eid:1"})], [2, 3])

    def test_digest_entry_and_margin(self):
        e = enf.digest_entry("**Subtitle replaced (TV):** Suits S01E01\nHow: whisperai (transcribed from the audio)\nBackup: `x`")
        self.assertEqual((e["kind"], e["title"], e["how"]), ("replaced", "Suits S01E01", "whisperai (transcribed from the audio)"))
        e = enf.digest_entry("**Subtitle replaced:** Aliens (1986)\nHow: subsource, as downloaded")
        self.assertEqual((e["kind"], e["title"]), ("replaced", "Aliens (1986)"))
        e = enf.digest_entry("**Subtitle unresolved:** Heat (1995): no candidate passed the check. Backup: `x`.")
        self.assertEqual((e["kind"], e["title"]), ("unresolved", "Heat (1995)"))
        self.assertEqual(enf.digest_entry("**Subtitle check (TV), needs review:** PLUTO S01E02: no English audio")["kind"], "review")
        # one stray window is set aside; with a single value that value is the margin
        self.assertAlmostEqual(enf.margin([-55.0, 0.1, 0.3, -0.2, 0.0, 0.05]), 0.3)
        self.assertEqual(enf.margin([None, None]), None)
        self.assertEqual(enf.margin([0.4]), 0.4)

    def test_digest_mode_collects_and_failures_still_post(self):
        posted = []
        enf.gate.discord = lambda m: posted.append(m)
        enf.DIGEST = []
        try:
            enf.say("**Subtitle replaced:** A (2000)\nHow: x", False)
            enf.say("**Subtitle check, failed:** A (2000): the replace step errored twice.", False)
            enf.say("**Subtitle sweep tranche done:** nothing.", False)
            got = enf.DIGEST
        finally:
            enf.DIGEST = None
        self.assertEqual([e["kind"] for e in got], ["replaced"])
        self.assertEqual(len(posted), 1)

    def test_backlog_lane_wiring(self):
        import tempfile
        st = {"since_ts": 0, "handled": [], "replaced": 0, "attempts": {}, "spot": [],
              "backlog": {"enabled": True, "digest": [], "digest_day": "", "since": "2026-01-01T00:00:00"}}
        posted, calls = [], []
        saved = {k: getattr(enf, k) for k in ("tv_core", "sweep", "tv_backlog_items", "watch_shows", "_load", "save_state", "PAUSE")}
        try:
            enf.PAUSE = os.path.join(tempfile.mkdtemp(), "no-such-pause")
            enf.gate.discord = lambda m: posted.append(m)
            enf.save_state = lambda s: None
            enf._load = lambda *a: object()
            enf.tv_backlog_items = lambda: [{"eid": 1, "show": "A", "season": 1, "episode": 1}]
            enf.watch_shows = lambda: ["A"]
            def fake_core(st_, slot, tvb, tv, work, dry, cm, cr, dc, bs):
                calls.append(("tv", [w["key"] for w in work], cr, dc))
                enf.say("**Subtitle replaced (TV):** A S01E01\nHow: opensubtitlescom via Bazarr", False)
                tvb["day_count"] += 1
                return 3, 1
            def fake_sweep(n, mins, st=None, max_replace=None):
                calls.append(("movies", max_replace))
                st["replaced"] += 1
                enf.say("**Subtitle replaced:** Heat (1995)\nHow: whisperai (transcribed from the audio)", False)
                return {"PASS": 4, "replaced": 1}
            enf.tv_core, enf.sweep = fake_core, fake_sweep
            enf.backlog_lane(st, False, 0.0 + __import__("time").time())
        finally:
            for k, v in saved.items():
                setattr(enf, k, v)
        self.assertEqual(calls[0], ("tv", ["eid:1"], enf.BL_PASS_REPLACE, enf.BL_DAILY_REPLACE))
        self.assertEqual(calls[1], ("movies", enf.BL_PASS_REPLACE))
        self.assertEqual(st["backlog_tv"]["day_count"], 2)                       # TV and movie replacements share the daily cap
        self.assertEqual((st["backlog"]["measured_tv"], st["backlog"]["measured_movies"]), (3, 5) if posted == [] else (0, 0))
        self.assertIsNone(enf.DIGEST)
        if posted:                                                              # it was after the digest hour, so the digest went out and was cleared
            self.assertEqual(len(posted), 1); self.assertIn("replaced 2", posted[0]); self.assertEqual(st["backlog"]["digest"], [])
        else:
            self.assertEqual(len(st["backlog"]["digest"]), 2)

    def test_audio_guard(self):
        mk = lambda *streams: json.dumps({"streams": [{"tags": x} for x in streams]})
        self.assertTrue(enf.audio_ok(mk({"language": "eng", "title": "Opus 7.1"}))[0])
        self.assertTrue(enf.audio_ok(mk({}))[0])                                  # single untagged track
        self.assertTrue(enf.audio_ok(mk({"language": "ita"}, {"language": "eng"}))[0])   # dub first, English later
        self.assertFalse(enf.audio_ok(mk({"language": "eng", "title": "Commentary by the director"}))[0])
        self.assertFalse(enf.audio_ok(mk({"language": "jpn"}))[0])
        self.assertFalse(enf.audio_ok(mk({}, {"language": "ita"}))[0])
        self.assertFalse(enf.audio_ok("")[0])
        self.assertFalse(enf.audio_ok(json.dumps({"streams": []}))[0])

    def test_pick_audio_index(self):
        S = lambda lang, title="": {"tags": {"language": lang, "title": title}}
        self.assertEqual(enf.pick_audio_index([S("ita"), S("eng")]), 1)
        self.assertEqual(enf.pick_audio_index([S("eng", "Commentary"), S("eng", "Main")]), 1)
        self.assertEqual(enf.pick_audio_index([S("eng"), S("ita")]), 0)
        self.assertIsNone(enf.pick_audio_index([S("ita")]))

    def test_plan_skips_old_and_handled(self):
        a, b, c = row(ts=50, path="/m/old"), row(ts=150, path="/m/handled"), row(ts=150, path="/m/new")
        st = {"handled": [enf.row_key(b)]}
        work = enf.plan_work([a, b, c], st, 100)
        self.assertEqual([w[1]["path"] for w in work], ["/m/new"])

    def test_order_candidates(self):
        M = lambda rid, title, year=None: {"rid": rid, "title": title, "year": year}
        movies = [M(1, "A"), M(2, "B"), M(3, "Cars", 2006), M(4, "Cars", 2011), M(5, "D")]
        order = lambda watch, done=(): [m["rid"] for m in enf.order_candidates(movies, watch, set(done))]
        self.assertEqual(order([("D", None), ("B", None)]), [5, 2, 1, 3, 4])            # watched first, newest first
        self.assertEqual(order([("Cars", None)]), [1, 2, 3, 4, 5])                      # ambiguous without a year: not guessed
        self.assertEqual(order([("Cars", "2011")]), [4, 1, 2, 3, 5])                    # year disambiguates
        self.assertEqual(order([("B", None)], done=["B", "A"]), [3, 4, 5])             # done titles are skipped
        self.assertEqual(order([("Unknown", None)]), [1, 2, 3, 4, 5])

    def test_spot_check_one_in_five_first_included(self):
        self.assertEqual([n for n in range(1, 12) if enf.spot_due(n)], [1, 6, 11])


if __name__ == "__main__":
    unittest.main()
