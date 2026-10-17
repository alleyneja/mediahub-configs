"""Run: python3 scripts/test_lidarr_ytdl_audit.py

Cases are the real tracks from the Oct 17 false-positive deletions (#80), as
(actual_s, musicbrainz_s, youtube_s, acoustid_score) -> expected verdict.
"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    "audit", os.path.join(os.path.dirname(__file__), "lidarr-ytdl-audit.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

CASES = {
    "jodeci freek'n you (truncated vs its source)": ((274, 379, 380, 0.99), "delete"),
    "jodeci get on up (truncated)": ((200, 248, 249, 0.0), "delete"),
    "midland i wish you would (+12%, verified)": ((194, 173, 173, 0.99), "review"),
    "logic my story (matches its source, +14%)": ((190, 166, 190, 0.0), "review"),
    "logic march 2007 (-11%, unverified)": ((138, 156, 0, 0.0), "review"),
    "eslabon besame (-10%)": ((190, 210, 0, 0.0), "review"),
    "ricky martin vuelve (+46% but AcoustID verified)": ((308, 211, 0, 0.99), "review"),
    "eslabon carmen (63s vs 189s, unverified)": ((63, 189, 0, 0.0), "delete"),
    "lofi compilation wrong audio": ((300, 70, 300, 0.0), "delete"),
    "good track": ((312, 311, 312, 0.96), "ok"),
    "good track within tolerance": ((276, 271, 0, 0.0), "ok"),
    "source padded, file matches MusicBrainz": ((379, 379, 395, 0.97), "ok"),
}


class Decide(unittest.TestCase):
    def test_cases(self):
        for name, (args, want) in CASES.items():
            with self.subTest(name):
                self.assertEqual(audit.decide(*args)[0], want)

    def test_last_resort_is_never_deleted(self):
        # a 200s cut of a source listed at 249s would be "truncated"...
        self.assertEqual(audit.decide(200, 248, 249, 0.0)[0], "delete")
        # ...unless the downloader documented it as a deliberate last resort
        self.assertEqual(audit.decide(200, 248, 249, 0.0, last_resort=True)[0], "review")
        # last resort never turns a good or review verdict into something else
        self.assertEqual(audit.decide(312, 311, 312, 0.96, last_resort=True)[0], "ok")
        self.assertEqual(audit.decide(194, 173, 173, 0.99, last_resort=True)[0], "review")

    def test_identity_failure_never_deletes(self):
        self.assertEqual(audit.decide(63, 189, 0, 0.0, identity_ok=False)[0], "unverifiable")

    def test_no_mb_length_is_not_a_crash(self):
        self.assertIn(audit.decide(100, 0, 0, 0.0)[0], ("delete", "review"))


class Identity(unittest.TestCase):
    def test_name_and_mtime(self):
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "02 - Freek'n You.mp3")
            open(p, "w").close()
            row = {"track_number": 2, "timestamp": time.time() - 30}
            self.assertTrue(audit.is_downloaders_file(p, row))
            self.assertFalse(audit.is_downloaders_file(p, {**row, "track_number": 3}))
            self.assertFalse(audit.is_downloaders_file(p, {**row, "timestamp": time.time() + 3600}))


if __name__ == "__main__":
    unittest.main()
