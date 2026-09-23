"""Time-window clustering and majority-vote reconciliation across N Whisper
transcription passes of the same audio.

See docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md.
No dependency on stable_whisper or faster-whisper — operates on plain word
dicts so it's testable without the model or Docker.
"""
import unicodedata


def _normalize(text: str) -> str:
    """Lowercase and strip Unicode punctuation, for vote comparison only.

    The winning candidate's original (non-normalized) text is what actually
    gets returned by reconcile_passes — this is comparison-only.
    """
    stripped = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    return stripped.strip().lower()


def _cluster_words_into_slots(words: list[dict], overlap_threshold: float) -> list[list[dict]]:
    """Group words from all passes into time slots by interval overlap.

    `words` must already be sorted by `start`. A word joins the current
    slot if its interval overlaps the slot's running union span by at
    least `overlap_threshold` of the SHORTER of the two spans (not IoU
    against their union) -- otherwise it opens a new slot.

    Overlap-over-shorter, not IoU, matters when passes segment the same
    sound at different granularity: if one pass says "hello" as a single
    0.0-1.0s word and another splits it into "hel" 0.0-0.3s + "lo"
    0.3-1.0s, IoU("hel", "hello") is only ~0.3 (penalized by the size
    mismatch) and would wrongly start a new slot, losing "hel" to quorum
    even though it's clearly the same sound. Overlap-over-shorter gives
    "hel" vs "hello" a full 1.0 (0.3s overlap / 0.3s "hel" duration), so
    finer splits still join the coarser word's slot.
    """
    slots: list[list[dict]] = []
    slot_span = None  # (start, end) union span of the current slot

    for word in words:
        ratio = 0.0
        if slot_span is not None:
            overlap = max(0.0, min(slot_span[1], word["end"]) - max(slot_span[0], word["start"]))
            word_duration = word["end"] - word["start"]
            slot_duration = slot_span[1] - slot_span[0]
            shorter = min(word_duration, slot_duration)
            ratio = overlap / shorter if shorter > 0 else 0.0

        if slot_span is not None and ratio >= overlap_threshold:
            slots[-1].append(word)
            slot_span = (min(slot_span[0], word["start"]), max(slot_span[1], word["end"]))
        else:
            slots.append([word])
            slot_span = (word["start"], word["end"])

    return slots


def _vote_slot(slot: list[dict]) -> dict | None:
    """Pick the winning word for one time slot, or None if quorum isn't met.

    Quorum: at least 2 distinct passes must have contributed a word to this
    slot, or it's dropped (last-resort transcription's main failure mode is
    hallucinating plausible dialogue over near-silence; a single dissenting
    pass is more likely to be that than a genuine catch).

    Among slots that meet quorum: majority vote on normalized text: ties
    (including full N-way disagreement) are broken by the single candidate
    with the highest probability.
    """
    contributing_passes = {word["pass_id"] for word in slot}
    if len(contributing_passes) < 2:
        return None

    groups: dict[str, list[dict]] = {}
    for word in slot:
        groups.setdefault(_normalize(word["word"]), []).append(word)

    best_key = max(groups, key=lambda k: (len(groups[k]), sum(word["probability"] for word in groups[k])))
    winner = max(groups[best_key], key=lambda word: word["probability"])
    return winner


def _resolve_overlaps(winners: list[dict]) -> list[dict]:
    """Guarantee the final winners list has no overlapping adjacent words.

    `winners` must already be sorted by `start`. Adjacent slots can still
    win overlapping timestamps -- e.g. under a systematic timing skew
    between passes, clustering can assign a word to a slightly-wrong slot.
    Rather than silently relying on stable_whisper's own force_order()
    repair downstream (which fixes the symptom but not deliberately, and
    isn't exercised by this module's own tests), reconcile_passes
    guarantees non-overlap itself: an overlapping winner whose normalized
    text matches the previous winner is a duplicate and dropped; an
    overlapping winner with different text has its start clamped forward
    to the previous winner's end.
    """
    resolved: list[dict] = []
    for winner in winners:
        if resolved and winner["start"] < resolved[-1]["end"]:
            if _normalize(winner["word"]) == _normalize(resolved[-1]["word"]):
                continue
            winner = {**winner, "start": resolved[-1]["end"]}
            if winner["start"] > winner["end"]:
                winner = {**winner, "end": winner["start"]}
        resolved.append(winner)
    return resolved


def group_into_segments(words: list[dict], max_gap: float = 0.5) -> list[list[dict]]:
    """Split a flat, time-sorted word list into segments at silence gaps.

    `words` must already be sorted by `start`. A new segment starts
    whenever the gap between one word's `end` and the next word's `start`
    exceeds `max_gap` seconds.

    Reassembling reconciled words as a SINGLE stable_whisper segment (one
    list containing every word) throws away all of Whisper's own
    utterance boundaries -- CUSTOM_REGROUP's length-based splitting then
    has nothing but character count to go on, and cuts the resulting
    multi-minute blob into evenly-sized chunks regardless of where the
    actual pauses are. Restoring gap-based segment boundaries before
    regroup runs is what gives regroup real utterances to work with again,
    the same shape of input the single-pass path always produced.
    """
    if not words:
        return []

    segments: list[list[dict]] = [[words[0]]]
    for previous, current in zip(words, words[1:]):
        if current["start"] - previous["end"] > max_gap:
            segments.append([])
        segments[-1].append(current)

    return segments


def temperature_ladder(passes: int) -> list[tuple]:
    """Per-pass decoding temperature ladder.

    Each pass gets a TUPLE starting at its own bias temperature and
    continuing faster-whisper's standard 0.2-step fallback up to 1.0, so
    every pass keeps automatic retry-on-failure for segments that fail
    the compression-ratio/logprob checks -- a bare float would silently
    disable that safety net entirely for that pass, which is not
    equivalent to today's un-passed default (faster-whisper's own default
    IS the full fallback tuple). Passes still start from different
    biases so they diverge on confident audio too, not only on segments
    Whisper's own fallback already flagged as risky.

    3 passes (the default) gives starting biases [0.0, 0.3, 0.6], each
    followed by the remaining standard steps up to 1.0.
    """
    standard_fallback = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    if passes <= 1:
        return [tuple(standard_fallback)]

    step = min(0.3, 1.0 / (passes - 1))
    starts = [round(min(i * step, 1.0), 2) for i in range(passes)]

    ladders = []
    for start in starts:
        remaining = [t for t in standard_fallback if t > start + 1e-9]
        ladders.append(tuple([start] + remaining))
    return ladders


def reconcile_passes(passes: list[list[dict]], overlap_threshold: float = 0.5) -> list[dict]:
    """Reconcile N transcription passes of the same audio into one word list.

    `passes`: a list of passes, each a list of word dicts with `start`,
    `end`, `word`, `probability`, already sorted by `start` within that
    pass. Requires at least 2 passes to produce any output at all — a
    single pass has nothing to vote against and every slot will fail
    quorum; callers with fewer than 2 successful passes should use that
    pass's raw result directly instead of calling this.

    Returns the winning words, sorted by `start`.
    """
    pooled = []
    for pass_id, pass_words in enumerate(passes):
        for word in pass_words:
            pooled.append({**word, "pass_id": pass_id})
    pooled.sort(key=lambda word: word["start"])

    slots = _cluster_words_into_slots(pooled, overlap_threshold)

    winners = []
    for slot in slots:
        winner = _vote_slot(slot)
        if winner is not None:
            winners.append({
                "start": winner["start"],
                "end": winner["end"],
                "word": winner["word"],
                "probability": winner["probability"],
            })

    winners.sort(key=lambda word: word["start"])
    return _resolve_overlaps(winners)
