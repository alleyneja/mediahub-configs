"""Word Error Rate / Character Error Rate scoring for comparing generated
subtitles against a ground-truth reference.

Hand-rolled (no jiwer/numpy -- neither is installable in this environment,
no pip). WER assumes reference and hypothesis are space-delimited words,
which holds for English and for translated text, but NOT for un-spaced
Japanese -- use CER for that case instead, since it operates on raw
characters and needs no tokenization.
"""
import unicodedata


def _normalize(text: str) -> str:
    """NFKC-normalize, lowercase, strip punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    return " ".join(text.split())


def _edit_ops(ref: list, hyp: list) -> tuple:
    """Levenshtein alignment with operation breakdown (substitutions,
    deletions, insertions) via full DP table + backtrace. O(n*m) time and
    space -- fine for word-level sequences (hundreds to low thousands of
    tokens), not intended for character-level use on long texts.
    """
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])

    i, j = n, m
    substitutions = deletions = insertions = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1

    return substitutions, deletions, insertions


def _edit_distance(ref: list, hyp: list) -> int:
    """Levenshtein distance only (no operation breakdown), O(n*m) time but
    O(min(n,m)) space via a rolling row -- for character-level sequences,
    where full O(n*m) space (word-level's _edit_ops) would blow up memory
    on full-episode transcripts (tens of thousands of characters).
    """
    if len(ref) < len(hyp):
        ref, hyp = hyp, ref
    n, m = len(ref), len(hyp)
    previous = list(range(m + 1))
    for i in range(1, n + 1):
        current = [i] + [0] * m
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                current[j] = previous[j - 1]
            else:
                current[j] = 1 + min(previous[j - 1], previous[j], current[j - 1])
        previous = current
    return previous[m]


def word_error_rate(reference: str, hypothesis: str) -> dict:
    """WER between two strings, word-tokenized on whitespace after
    normalization. Assumes space-delimited words -- not meaningful for
    un-spaced Japanese text (use char_error_rate for that).
    """
    ref_tokens = _normalize(reference).split()
    hyp_tokens = _normalize(hypothesis).split()
    substitutions, deletions, insertions = _edit_ops(ref_tokens, hyp_tokens)
    n = len(ref_tokens)
    if n == 0:
        wer = 0.0 if not hyp_tokens else float("inf")
    else:
        wer = (substitutions + deletions + insertions) / n
    return {
        "wer": wer,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "ref_word_count": n,
    }


def char_error_rate(reference: str, hypothesis: str) -> dict:
    """CER between two strings, at the raw character level after
    normalization -- no tokenization, so it works for languages (Japanese)
    that don't delimit words with whitespace.
    """
    ref_chars = list(_normalize(reference))
    hyp_chars = list(_normalize(hypothesis))
    n = len(ref_chars)
    if n == 0:
        cer = 0.0 if not hyp_chars else float("inf")
        distance = len(hyp_chars)
    else:
        distance = _edit_distance(ref_chars, hyp_chars)
        cer = distance / n
    return {
        "cer": cer,
        "edit_distance": distance,
        "ref_char_count": n,
    }
