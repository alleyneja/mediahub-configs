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
    """Group words from all passes into time slots by interval overlap (IoU).

    `words` must already be sorted by `start`. A word joins the current
    slot if its interval overlaps the slot's running union span by at
    least `overlap_threshold` (IoU); otherwise it opens a new slot.
    """
    slots: list[list[dict]] = []
    slot_span = None  # (start, end) union span of the current slot

    for word in words:
        iou = 0.0
        if slot_span is not None:
            overlap = max(0.0, min(slot_span[1], word["end"]) - max(slot_span[0], word["start"]))
            union = max(slot_span[1], word["end"]) - min(slot_span[0], word["start"])
            iou = overlap / union if union > 0 else 0.0

        if slot_span is not None and iou >= overlap_threshold:
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
    return winners
