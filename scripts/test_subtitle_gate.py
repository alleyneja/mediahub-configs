"""Tests for subtitle-gate.py rule v1 (run: python3 -m unittest scripts/test_subtitle_gate.py)."""
import importlib.util, os, unittest

spec = importlib.util.spec_from_file_location("gate", os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitle-gate.py"))
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)


def rec(offsets, dur=6000, last=5900):
    return {"dur": dur, "last_cue_end": last, "windows": [None if o is None else {"offset": o, "score": 1, "cues": 50, "at_min": 1} for o in offsets]}


class Rule(unittest.TestCase):
    def test_clean_pass(self):
        self.assertEqual(gate.judge(rec([0.0, 0.1, -0.1, 0.0, 0.1, 0.0, 0.1]))[0], "PASS")

    def test_artifact_windows_tolerated_with_five_usable(self):
        self.assertEqual(gate.judge(rec([0.0, 58.0, 0.1, 0.0, 50.0, 0.1, 0.0]))[0], "PASS")

    def test_constant_shift_fails(self):
        st, why = gate.judge(rec([20.0] * 7)); self.assertEqual(st, "FAIL"); self.assertIn("large offset", " ".join(why))

    def test_small_shift_fails(self):
        self.assertEqual(gate.judge(rec([-1.0] * 7))[0], "FAIL")

    def test_drift_fails(self):
        self.assertEqual(gate.judge(rec([0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8]))[0], "FAIL")

    def test_missing_window_fails(self):
        self.assertEqual(gate.judge(rec([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None]))[0], "FAIL")

    def test_short_coverage_fails(self):
        self.assertEqual(gate.judge(rec([0.0] * 7, dur=6000, last=5000))[0], "FAIL")

    def test_low_speech_is_unverifiable_not_fail(self):
        self.assertEqual(gate.judge(rec([43.9, 0.0, 0.1, 56.9, 55.0, 23.2, 0.0]))[0], "UNVERIFIABLE")

    def test_scattered_artifacts_alone_are_not_a_consensus(self):
        self.assertEqual(gate.judge(rec([0.0, 58.0, -30.0, 0.0, 25.0, 0.0, 0.0]))[0], "UNVERIFIABLE")


class Helpers(unittest.TestCase):
    def test_host_path(self):
        self.assertEqual(gate.host_path("/data/movies/X/X.en.srt"), "/mnt/media/movies/X/X.en.srt")


class PlexGuardQuery(unittest.TestCase):
    """The gate stops for active playback only; a paused session does not block it (Jay, 2026-11-16)."""
    def run_q(self, meta):
        import json as _j, subprocess as _s
        snippet = gate.PLEX_QUERY.split('python3 -c "', 1)[1].rsplit('"', 1)[0].replace('\\"', '"')
        data = _j.dumps({"MediaContainer": {"size": len(meta), "Metadata": meta}})
        return _s.run(["python3", "-c", snippet], input=data, capture_output=True, text=True).stdout.strip()

    def test_paused_does_not_block(self):
        P = lambda s: {"type": "episode", "Player": {"state": s}}
        self.assertEqual(self.run_q([]), "0")
        self.assertEqual(self.run_q([P("paused")]), "0")
        self.assertEqual(self.run_q([P("paused"), P("playing")]), "1")
        self.assertEqual(self.run_q([P("buffering")]), "1")
        self.assertEqual(self.run_q([{"type": "movie"}]), "1")      # no state field: counts as active


if __name__ == "__main__":
    unittest.main()
