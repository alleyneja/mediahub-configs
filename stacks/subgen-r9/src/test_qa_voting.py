import unittest

from qa_voting import (
    _cluster_words_into_slots,
    _normalize,
    _vote_slot,
    group_into_segments,
    reconcile_passes,
    temperature_ladder,
)


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

    def test_one_pass_word_fully_inside_two_split_words_from_another_pass_all_join_one_slot(self):
        # Pass A says "hello" as one word [0.0, 1.0]; pass B splits the same
        # sound into "hel" [0.0, 0.3] and "lo" [0.3, 1.0]. Against IoU, "hel"
        # only covers 30% of "hello"'s span and would wrongly start its own
        # slot (losing the word to quorum). Overlap relative to the SHORTER
        # interval covers this: overlap(hel, hello)=0.3, min(len)=0.3 -> 1.0.
        words = [w(0.0, 1.0, "hello", pass_id=0), w(0.0, 0.3, "hel", pass_id=1), w(0.3, 1.0, "lo", pass_id=1)]
        slots = _cluster_words_into_slots(words, overlap_threshold=0.5)
        self.assertEqual(len(slots), 1)
        self.assertEqual(len(slots[0]), 3)


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


class TestResolveOverlaps(unittest.TestCase):
    def test_drops_duplicate_word_when_adjacent_winners_overlap(self):
        from qa_voting import _resolve_overlaps
        winners = [w(0.0, 0.6, "foo", pass_id=0), w(0.5, 1.0, "foo", pass_id=1)]
        result = _resolve_overlaps(winners)
        self.assertEqual([r["word"] for r in result], ["foo"])

    def test_clamps_start_forward_when_adjacent_winners_differ_and_overlap(self):
        from qa_voting import _resolve_overlaps
        winners = [w(0.0, 0.6, "foo", pass_id=0), w(0.5, 1.0, "bar", pass_id=1)]
        result = _resolve_overlaps(winners)
        self.assertEqual([r["word"] for r in result], ["foo", "bar"])
        self.assertEqual(result[1]["start"], 0.6)
        self.assertLessEqual(result[0]["end"], result[1]["start"])

    def test_leaves_non_overlapping_winners_unchanged(self):
        from qa_voting import _resolve_overlaps
        winners = [w(0.0, 0.5, "foo"), w(0.5, 1.0, "bar")]
        result = _resolve_overlaps(winners)
        self.assertEqual(result, winners)


class TestGroupIntoSegments(unittest.TestCase):
    def test_single_gap_over_threshold_splits_into_two_segments(self):
        words = [w(0.0, 0.5, "foo"), w(0.6, 1.0, "bar"), w(5.0, 5.5, "baz")]
        segments = group_into_segments(words, max_gap=0.5)
        self.assertEqual(len(segments), 2)
        self.assertEqual([wd["word"] for wd in segments[0]], ["foo", "bar"])
        self.assertEqual([wd["word"] for wd in segments[1]], ["baz"])

    def test_gap_under_threshold_stays_one_segment(self):
        words = [w(0.0, 0.5, "foo"), w(0.7, 1.0, "bar")]
        segments = group_into_segments(words, max_gap=0.5)
        self.assertEqual(len(segments), 1)
        self.assertEqual(len(segments[0]), 2)

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(group_into_segments([], max_gap=0.5), [])

    def test_no_words_lost_across_segment_boundaries(self):
        words = [w(0.0, 0.5, "a"), w(3.0, 3.5, "b"), w(3.6, 4.0, "c"), w(10.0, 10.5, "d")]
        segments = group_into_segments(words, max_gap=0.5)
        flattened = [wd["word"] for seg in segments for wd in seg]
        self.assertEqual(flattened, ["a", "b", "c", "d"])


class TestTemperatureLadder(unittest.TestCase):
    def test_three_passes_gives_three_starting_biases(self):
        ladders = temperature_ladder(3)
        self.assertEqual(len(ladders), 3)
        self.assertEqual([ladder[0] for ladder in ladders], [0.0, 0.3, 0.6])

    def test_every_pass_keeps_fallback_up_to_one(self):
        # Each pass's ladder must end at 1.0 so faster-whisper's own
        # retry-on-failure safety net stays active for every pass, not
        # just a bare starting float that silently disables it.
        for ladder in temperature_ladder(3):
            self.assertEqual(ladder[-1], 1.0)

    def test_single_pass_gets_the_full_standard_fallback_ladder(self):
        ladders = temperature_ladder(1)
        self.assertEqual(ladders, [(0.0, 0.2, 0.4, 0.6, 0.8, 1.0)])

    def test_no_duplicate_temperature_within_one_passs_ladder(self):
        for ladder in temperature_ladder(5):
            self.assertEqual(len(ladder), len(set(ladder)))


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
