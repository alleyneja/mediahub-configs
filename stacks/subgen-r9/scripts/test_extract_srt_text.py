import tempfile
import unittest
from pathlib import Path

from extract_srt_text import extract_text


def write_srt(content: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


class TestExtractText(unittest.TestCase):
    def test_extracts_dialogue_from_simple_srt(self):
        path = write_srt(
            "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nGeneral Kenobi.\n\n"
        )
        self.assertEqual(extract_text(path), "Hello there. General Kenobi.")
        Path(path).unlink()

    def test_joins_multiline_cue_with_space(self):
        path = write_srt(
            "1\n00:00:01,000 --> 00:00:02,000\nLine one\nLine two\n\n"
        )
        self.assertEqual(extract_text(path), "Line one Line two")
        Path(path).unlink()

    def test_strips_html_and_ass_style_tags(self):
        path = write_srt(
            '1\n00:00:01,000 --> 00:00:02,000\n<font face="Arial"><b>{\\an8}Styled text</b></font>\n\n'
        )
        self.assertEqual(extract_text(path), "Styled text")
        Path(path).unlink()

    def test_empty_file_returns_empty_string(self):
        path = write_srt("")
        self.assertEqual(extract_text(path), "")
        Path(path).unlink()

    def test_skips_cues_that_become_empty_after_tag_stripping(self):
        path = write_srt(
            "1\n00:00:01,000 --> 00:00:02,000\n<font>{\\an8}</font>\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nReal line.\n\n"
        )
        self.assertEqual(extract_text(path), "Real line.")
        Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
