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
