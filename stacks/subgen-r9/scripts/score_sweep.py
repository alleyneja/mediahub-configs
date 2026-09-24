#!/usr/bin/env python3
"""Score every trial in a sweep output directory (sweep/<trial>/<episode>.srt)
against the ground-truth reference files that live in the original test
directories (round2/<episode>/embedded_clean.srt, or s23e24/embedded_japanese.srt).

Usage: score_sweep.py <sweep_dir> <testdata_root>
"""
import sys
from pathlib import Path

from extract_srt_text import extract_text
from wer_cer import word_error_rate, char_error_rate

REFERENCE_CANDIDATES = ["embedded_clean.srt", "embedded_japanese.srt", "embedded.srt"]
BASELINE_CANDIDATES = ["single_pass.srt", "single_pass_baseline.srt"]

EPISODE_DIR = {
    "s23e24": "s23e24",
    "op-s23e20": "round2/op-s23e20",
    "frieren-s02e03": "round2/frieren-s02e03",
    "csm-s01e11": "round2/csm-s01e11",
    "ehc-s01e01": "round2/ehc-s01e01",
    "bobs-s10e06": "round2/bobs-s10e06",
    "dexter-s02e08": "round2/dexter-s02e08",
}


def find_reference(testdata_root: Path, episode: str) -> Path:
    episode_dir = testdata_root / EPISODE_DIR[episode]
    for name in REFERENCE_CANDIDATES:
        candidate = episode_dir / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No reference file for {episode} in {episode_dir}")


def find_baseline(testdata_root: Path, episode: str) -> Path:
    episode_dir = testdata_root / EPISODE_DIR[episode]
    for name in BASELINE_CANDIDATES:
        candidate = episode_dir / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No baseline single-pass file for {episode} in {episode_dir}")


def _fmt_pct(value: float) -> str:
    return "inf" if value == float("inf") else f"{value * 100:.1f}%"


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    sweep_dir = Path(sys.argv[1])
    testdata_root = Path(sys.argv[2])

    trials = sorted(p.name for p in sweep_dir.iterdir() if p.is_dir())
    episodes = sorted(EPISODE_DIR.keys())

    # baseline row first
    rows = []
    for episode in episodes:
        ref_text = extract_text(str(find_reference(testdata_root, episode)))
        hyp_text = extract_text(str(find_baseline(testdata_root, episode)))
        wer = word_error_rate(ref_text, hyp_text)["wer"]
        cer = char_error_rate(ref_text, hyp_text)["cer"]
        rows.append(("baseline", episode, wer, cer))

    for trial in trials:
        for episode in episodes:
            hyp_path = sweep_dir / trial / f"{episode}.srt"
            if not hyp_path.exists():
                print(f"# missing {hyp_path}, skipping", file=sys.stderr)
                continue
            ref_text = extract_text(str(find_reference(testdata_root, episode)))
            hyp_text = extract_text(str(hyp_path))
            wer = word_error_rate(ref_text, hyp_text)["wer"]
            cer = char_error_rate(ref_text, hyp_text)["cer"]
            rows.append((trial, episode, wer, cer))

    trial_w = max(len(r[0]) for r in rows) + 2
    ep_w = max(len(r[1]) for r in rows) + 2
    print(f"{'trial':<{trial_w}}{'episode':<{ep_w}}{'WER':>8}{'CER':>8}")
    for trial, episode, wer, cer in rows:
        print(f"{trial:<{trial_w}}{episode:<{ep_w}}{_fmt_pct(wer):>8}{_fmt_pct(cer):>8}")

    print()
    print("=== Per-trial averages (mean across 7 episodes) ===")
    by_trial = {}
    for trial, episode, wer, cer in rows:
        by_trial.setdefault(trial, []).append((wer, cer))
    order = ["baseline"] + trials
    for trial in order:
        if trial not in by_trial:
            continue
        scores = by_trial[trial]
        finite_wer = [w for w, c in scores if w != float("inf")]
        finite_cer = [c for w, c in scores if c != float("inf")]
        avg_wer = sum(finite_wer) / len(finite_wer) if finite_wer else float("inf")
        avg_cer = sum(finite_cer) / len(finite_cer) if finite_cer else float("inf")
        print(f"{trial:<{trial_w}}avg WER={_fmt_pct(avg_wer)}  avg CER={_fmt_pct(avg_cer)}")


if __name__ == "__main__":
    main()
