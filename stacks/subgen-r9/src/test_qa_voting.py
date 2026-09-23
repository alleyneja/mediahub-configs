import unittest

from qa_voting import _cluster_words_into_slots, _normalize, _vote_slot, reconcile_passes


def w(start, end, word, probability=0.9, pass_id=0):
    return {"start": start, "end": end, "word": word, "probability": probability, "pass_id": pass_id}


class TestNormalize(unittest.TestCase):
    def test_lowercases_and_strips_ascii_punctuation(self):
        self.assertEqual(_normalize("Hello,"), "hello")

    def test_strips_unicode_punctuation_without_touching_letters(self):
        # Japanese full-width comma/period (U+3001, U+3002) around real text.
        self.assertEqual(_normalize("こんにちは。"), "こんにちは")


class TestClustering(unittest.TestCase):
    def test_non_overlapping_words_become_separate_slots(self):
        words = [w(0.0, 0.5, "one"), w(10.0, 10.5, "two")]
        slots = _cluster_words_into_slots(words, overlap_threshold=0.5)
        self.assertEqual(len(slots), 2)

    def test_heavily_overlapping_words_become_one_slot(self):
        words = [w(0.0, 1.0, "hello", pass_id=0), w(0.05, 1.05, "hello", pass_id=1)]
        slots = _cluster_words_into_slots(words, overlap_threshold=0.5)
        self.assertEqual(len(slots), 1)
        self.assertEqual(len(slots[0]), 2)

    def test_partial_overlap_below_threshold_creates_new_slot(self):
        # [0.0, 1.0] and [0.9, 1.9]: overlap 0.1, union 1.9 -> IoU ~0.053, below 0.5
        words = [w(0.0, 1.0, "hello", pass_id=0), w(0.9, 1.9, "world", pass_id=1)]
        slots = _cluster_words_into_slots(words, overlap_threshold=0.5)
        self.assertEqual(len(slots), 2)


class TestVoteSlot(unittest.TestCase):
    def test_majority_wins_over_minority(self):
        slot = [w(0, 1, "hello", pass_id=0), w(0, 1, "hello", pass_id=1), w(0, 1, "hallo", pass_id=2)]
        winner = _vote_slot(slot)
        self.assertEqual(winner["word"], "hello")

    def test_case_and_punctuation_do_not_split_the_vote(self):
        slot = [w(0, 1, "Hello,", pass_id=0), w(0, 1, "hello", pass_id=1), w(0, 1, "HELLO!", pass_id=2)]
        winner = _vote_slot(slot)
        self.assertEqual(_normalize(winner["word"]), "hello")

    def test_three_way_split_falls_back_to_highest_probability(self):
        slot = [
            w(0, 1, "aaa", probability=0.2, pass_id=0),
            w(0, 1, "bbb", probability=0.9, pass_id=1),
            w(0, 1, "ccc", probability=0.4, pass_id=2),
        ]
        winner = _vote_slot(slot)
        self.assertEqual(winner["word"], "bbb")

    def test_quorum_drops_slot_with_only_one_contributing_pass(self):
        slot = [w(0, 1, "hallucinated", pass_id=0)]
        self.assertIsNone(_vote_slot(slot))

    def test_two_of_three_contributing_meets_quorum(self):
        slot = [w(0, 1, "real", pass_id=0), w(0, 1, "real", pass_id=1)]
        self.assertIsNotNone(_vote_slot(slot))

    def test_winner_keeps_real_timestamps_and_probability_not_mangled(self):
        # Both candidates agree on the word; the higher-probability one's own
        # timestamps/probability should come through untouched (ties within
        # an agreeing group are broken by probability same as cross-group ties).
        slot = [w(3.2, 3.9, "hello", probability=0.81, pass_id=0), w(3.21, 3.91, "hello", probability=0.77, pass_id=1)]
        winner = _vote_slot(slot)
        self.assertEqual(winner["start"], 3.2)
        self.assertEqual(winner["end"], 3.9)
        self.assertEqual(winner["probability"], 0.81)


class TestReconcilePasses(unittest.TestCase):
    def test_output_sorted_by_start_even_if_passes_interleave(self):
        pass_a = [w(0.0, 0.5, "first"), w(2.0, 2.5, "third")]
        pass_b = [w(1.0, 1.5, "second"), w(0.02, 0.52, "first")]
        result = reconcile_passes([pass_a, pass_b], overlap_threshold=0.5)
        # "second" and "third" only appear in one pass each -> dropped by quorum;
        # "first" appears (overlapping) in both -> kept.
        self.assertEqual([r["word"] for r in result], ["first"])

    def test_full_agreement_across_three_passes_produces_full_sentence(self):
        sentence = [w(0.0, 0.3, "we"), w(0.3, 0.6, "are"), w(0.6, 1.0, "here")]
        passes = [sentence, sentence, sentence]
        result = reconcile_passes(passes, overlap_threshold=0.5)
        self.assertEqual([r["word"] for r in result], ["we", "are", "here"])

    def test_no_pass_data_returns_empty(self):
        self.assertEqual(reconcile_passes([], overlap_threshold=0.5), [])


if __name__ == "__main__":
    unittest.main()
