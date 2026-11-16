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

    def test_spot_check_one_in_five_first_included(self):
        self.assertEqual([n for n in range(1, 12) if enf.spot_due(n)], [1, 6, 11])


if __name__ == "__main__":
    unittest.main()
