import tempfile
import unittest
from pathlib import Path

from score_subtitles import find_reference, find_hypotheses


class TestFindReference(unittest.TestCase):
    def test_finds_embedded_clean_first(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["embedded_clean.srt", "embedded_japanese.srt"]:
                (Path(d) / name).write_text("x")
            self.assertEqual(find_reference(d).name, "embedded_clean.srt")

    def test_falls_back_to_embedded_japanese(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "embedded_japanese.srt").write_text("x")
            self.assertEqual(find_reference(d).name, "embedded_japanese.srt")

    def test_returns_none_if_no_reference_found(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(find_reference(d))


class TestFindHypotheses(unittest.TestCase):
    def test_finds_both_single_and_multi_pass(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "single_pass.srt").write_text("x")
            (Path(d) / "multi_pass.srt").write_text("x")
            names = sorted(p.name for p in find_hypotheses(d))
            self.assertEqual(names, ["multi_pass.srt", "single_pass.srt"])

    def test_finds_round1_naming_variants(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "single_pass_baseline.srt").write_text("x")
            (Path(d) / "multi_pass_voted.srt").write_text("x")
            names = sorted(p.name for p in find_hypotheses(d))
            self.assertEqual(names, ["multi_pass_voted.srt", "single_pass_baseline.srt"])

    def test_empty_list_if_none_found(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(find_hypotheses(d), [])


if __name__ == "__main__":
    unittest.main()
