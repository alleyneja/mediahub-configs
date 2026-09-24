import unittest

from wer_cer import word_error_rate, char_error_rate, _normalize


class TestNormalize(unittest.TestCase):
    def test_lowercases_and_strips_punctuation(self):
        self.assertEqual(_normalize("Hello, World!"), "hello world")

    def test_collapses_whitespace(self):
        self.assertEqual(_normalize("hello    world\n\nfoo"), "hello world foo")

    def test_nfkc_normalizes_fullwidth_forms(self):
        # Fullwidth "Ａ" (U+FF21) should normalize to ASCII "a" (after lowercasing)
        self.assertEqual(_normalize("ＡＢＣ"), "abc")


class TestWordErrorRate(unittest.TestCase):
    def test_identical_text_has_zero_wer(self):
        result = word_error_rate("the cat sat on the mat", "the cat sat on the mat")
        self.assertEqual(result["wer"], 0.0)
        self.assertEqual(result["substitutions"], 0)
        self.assertEqual(result["deletions"], 0)
        self.assertEqual(result["insertions"], 0)

    def test_single_substitution(self):
        result = word_error_rate("the cat sat on the mat", "the cat sit on the mat")
        self.assertEqual(result["substitutions"], 1)
        self.assertEqual(result["deletions"], 0)
        self.assertEqual(result["insertions"], 0)
        self.assertAlmostEqual(result["wer"], 1 / 6)

    def test_single_deletion(self):
        result = word_error_rate("the cat sat on the mat", "the cat on the mat")
        self.assertEqual(result["substitutions"], 0)
        self.assertEqual(result["deletions"], 1)
        self.assertEqual(result["insertions"], 0)
        self.assertAlmostEqual(result["wer"], 1 / 6)

    def test_single_insertion(self):
        result = word_error_rate("the cat sat on the mat", "the cat sat right on the mat")
        self.assertEqual(result["substitutions"], 0)
        self.assertEqual(result["deletions"], 0)
        self.assertEqual(result["insertions"], 1)
        self.assertAlmostEqual(result["wer"], 1 / 6)

    def test_case_and_punctuation_do_not_count_as_errors(self):
        result = word_error_rate("The cat sat.", "the cat sat")
        self.assertEqual(result["wer"], 0.0)

    def test_empty_reference_and_hypothesis_is_zero_wer(self):
        result = word_error_rate("", "")
        self.assertEqual(result["wer"], 0.0)
        self.assertEqual(result["ref_word_count"], 0)

    def test_empty_reference_nonempty_hypothesis_is_infinite_wer(self):
        result = word_error_rate("", "hello world")
        self.assertEqual(result["wer"], float("inf"))
        self.assertEqual(result["insertions"], 2)

    def test_completely_wrong_hypothesis_has_wer_above_one(self):
        # More insertions than reference words pushes WER over 100%.
        result = word_error_rate("hi", "completely different extra words here")
        self.assertGreater(result["wer"], 1.0)


class TestCharErrorRate(unittest.TestCase):
    def test_identical_text_has_zero_cer(self):
        result = char_error_rate("hello world", "hello world")
        self.assertEqual(result["cer"], 0.0)

    def test_single_character_substitution(self):
        result = char_error_rate("hello", "hallo")
        self.assertAlmostEqual(result["cer"], 1 / 5)

    def test_japanese_text_no_whitespace_tokenization_needed(self):
        # CER operates on raw characters, so it works even without spaces
        # between words -- unlike WER, which assumes space-delimited tokens
        # and is not meaningful for un-spaced Japanese text.
        result = char_error_rate("こんにちは世界", "こんにちは世界")
        self.assertEqual(result["cer"], 0.0)
        result2 = char_error_rate("こんにちは世界", "こんばんは世界")
        self.assertGreater(result2["cer"], 0.0)

    def test_empty_reference_and_hypothesis_is_zero_cer(self):
        result = char_error_rate("", "")
        self.assertEqual(result["cer"], 0.0)

    def test_empty_reference_nonempty_hypothesis_is_infinite_cer(self):
        result = char_error_rate("", "abc")
        self.assertEqual(result["cer"], float("inf"))


if __name__ == "__main__":
    unittest.main()
