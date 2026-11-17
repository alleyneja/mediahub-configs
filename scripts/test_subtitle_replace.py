import importlib.util, json, os, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("rep", os.path.join(HERE, "subtitle-replace.py"))
rep = importlib.util.module_from_spec(spec); spec.loader.exec_module(rep)


class T(unittest.TestCase):
    def test_dangling(self):
        rows = [{"title": "A", "event": "start"}, {"title": "A", "event": "end"},
                {"title": "B", "event": "start"},
                {"title": "C", "event": "start"}, {"title": "C", "event": "candidate", "state": "PASS"}]
        d = rep.dangling_titles(rows)
        self.assertEqual(sorted(d), ["B", "C"])
        self.assertFalse(d["B"][1]); self.assertTrue(d["C"][1])
        # a later start for the same title reopens it
        self.assertIn("A", rep.dangling_titles(rows + [{"title": "A", "event": "start"}]))

    def _setup(self, tmp, live_bytes):
        rep.ROOT = tmp; rep.LEDGER = os.path.join(tmp, "ledger.jsonl")
        lib = os.path.join(tmp, "lib"); os.makedirs(lib)
        orig = os.path.join(lib, "M.en.srt")
        bak_dir = os.path.join(tmp, "backups", "2026-01-01", "M"); os.makedirs(bak_dir)
        open(os.path.join(bak_dir, "M.en.srt"), "wb").write(b"original-sub")
        json.dump({"title": "M", "orig_path": orig}, open(os.path.join(bak_dir, "manifest.json"), "w"))
        if live_bytes is not None:
            open(orig, "wb").write(live_bytes)
        return orig

    def _ledger(self, rows):
        open(rep.LEDGER, "w").write("".join(json.dumps(r) + "\n" for r in rows))

    def test_recover_missing_and_unvetted(self):
        for live in (None, b"unvetted-candidate"):
            with tempfile.TemporaryDirectory() as tmp:
                orig = self._setup(tmp, live)
                self._ledger([{"title": "M", "event": "start", "tier": "B"}])
                rep.recover()
                self.assertEqual(open(orig, "rb").read(), b"original-sub")
                rows = [json.loads(l) for l in open(rep.LEDGER)]
                self.assertEqual(rows[-1]["event"], "recovered"); self.assertEqual(rows[-1]["action"], "restored")
                rep.recover()                                  # closed now: second run does nothing
                self.assertEqual(len(open(rep.LEDGER).readlines()), 2)

    def test_recover_leaves_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = self._setup(tmp, b"accepted-new-sub")
            self._ledger([{"title": "M", "event": "start"}, {"title": "M", "event": "candidate", "state": "PASS"}])
            rep.recover()
            self.assertEqual(open(orig, "rb").read(), b"accepted-new-sub")

    def test_guard_per_title(self):
        d = rep.parse_sessions('{"files": ["Shrek (2001).mkv"], "movies": 1, "all": 3}')
        self.assertIsNone(rep.guard_decision(d, "Other (2000).mkv"))                       # another movie is playing: go ahead
        self.assertTrue(rep.guard_decision(d, "Shrek (2001).mkv").startswith(rep.THIS_MOVIE_MSG))
        self.assertIn("1 movie stream", rep.guard_decision(d, None))                        # nothing picked yet: any movie stream stops
        self.assertIsNone(rep.guard_decision(d, "Shrek (2001).mkv", allow=True))            # Jay's one-off override
        unknown = rep.parse_sessions('{"files": [], "movies": 1, "all": 1}')               # a movie session with no readable file
        self.assertTrue(rep.guard_decision(unknown, "Other (2000).mkv").startswith("STOP"))
        self.assertIsNone(rep.guard_decision(rep.parse_sessions('{"files": [], "movies": 0, "all": 2}'), "X.mkv"))   # only episodes
        self.assertIsNone(rep.parse_sessions("garbage"))
        self.assertIsNone(rep.parse_sessions('{"files": []}'))


if __name__ == "__main__":
    unittest.main()
