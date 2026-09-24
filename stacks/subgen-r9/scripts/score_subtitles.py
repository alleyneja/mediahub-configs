#!/usr/bin/env python3
"""Score generated subtitles against an embedded ground-truth track using
WER (Word Error Rate) and CER (Character Error Rate) -- a quantitative
replacement for manual read-through comparison.

Usage:
  score_subtitles.py <reference.srt> <hypothesis.srt>
  score_subtitles.py --batch <dir> [<dir> ...]

--batch scans each directory for a reference file (embedded_clean.srt /
embedded_japanese.srt / embedded.srt, in that preference order) and any
hypothesis files (single_pass.srt, single_pass_baseline.srt,
multi_pass.srt, multi_pass_voted.srt), and prints a summary table scoring
every hypothesis found against that directory's reference.

WER assumes space-delimited words -- meaningful for English and for
translated text, but NOT for un-spaced Japanese (raw transcribe-mode
output in the source language). CER has no such assumption and is the
more reliable metric there. For translate-mode output (Japanese audio ->
English translation), WER is still computed but is inherently noisy:
two valid translations of the same line can use different correct words,
which WER counts as errors even when the meaning is right -- read it as
a relative signal (did A score better than B), not an absolute quality
grade.
"""
import argparse
import sys
from pathlib import Path

from extract_srt_text import extract_text
from wer_cer import word_error_rate, char_error_rate

REFERENCE_CANDIDATES = ["embedded_clean.srt", "embedded_japanese.srt", "embedded.srt"]
HYPOTHESIS_CANDIDATES = [
    "single_pass.srt",
    "single_pass_baseline.srt",
    "multi_pass.srt",
    "multi_pass_voted.srt",
]


def find_reference(dir_path) -> Path | None:
    d = Path(dir_path)
    for name in REFERENCE_CANDIDATES:
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def find_hypotheses(dir_path) -> list:
    d = Path(dir_path)
    return [d / name for name in HYPOTHESIS_CANDIDATES if (d / name).exists()]


def score_pair(reference_path, hypothesis_path) -> dict:
    ref_text = extract_text(str(reference_path))
    hyp_text = extract_text(str(hypothesis_path))
    wer_result = word_error_rate(ref_text, hyp_text)
    cer_result = char_error_rate(ref_text, hyp_text)
    return {"wer": wer_result, "cer": cer_result}


def _fmt_pct(value: float) -> str:
    if value == float("inf"):
        return "inf"
    return f"{value * 100:.1f}%"


def run_single(reference_path: str, hypothesis_path: str) -> None:
    result = score_pair(reference_path, hypothesis_path)
    wer, cer = result["wer"], result["cer"]
    print(f"Reference:  {reference_path}")
    print(f"Hypothesis: {hypothesis_path}")
    print()
    print(f"WER: {_fmt_pct(wer['wer'])}  "
          f"(sub={wer['substitutions']} del={wer['deletions']} ins={wer['insertions']} "
          f"of {wer['ref_word_count']} reference words)")
    print(f"CER: {_fmt_pct(cer['cer'])}  "
          f"(edit_distance={cer['edit_distance']} of {cer['ref_char_count']} reference chars)")


def run_batch(dir_paths: list) -> None:
    rows = []
    for dir_path in dir_paths:
        reference = find_reference(dir_path)
        if reference is None:
            print(f"# {dir_path}: no reference file found, skipping", file=sys.stderr)
            continue
        for hypothesis in find_hypotheses(dir_path):
            result = score_pair(reference, hypothesis)
            rows.append((Path(dir_path).name, hypothesis.name, result["wer"]["wer"], result["cer"]["cer"]))

    if not rows:
        print("No episodes scored -- no reference/hypothesis files found.", file=sys.stderr)
        return

    name_w = max(len(r[0]) for r in rows) + 2
    hyp_w = max(len(r[1]) for r in rows) + 2
    print(f"{'episode':<{name_w}}{'hypothesis':<{hyp_w}}{'WER':>8}{'CER':>8}")
    for episode, hyp_name, wer, cer in rows:
        print(f"{episode:<{name_w}}{hyp_name:<{hyp_w}}{_fmt_pct(wer):>8}{_fmt_pct(cer):>8}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference", nargs="?", help="Reference (ground truth) .srt file")
    parser.add_argument("hypothesis", nargs="?", help="Hypothesis (generated) .srt file")
    parser.add_argument("--batch", nargs="+", metavar="DIR", help="Score all episodes in the given directories")
    args = parser.parse_args()

    if args.batch:
        run_batch(args.batch)
    elif args.reference and args.hypothesis:
        run_single(args.reference, args.hypothesis)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
