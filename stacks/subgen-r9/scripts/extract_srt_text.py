"""Extract concatenated dialogue text from an .srt file, for WER/CER
scoring. WER/CER are computed on the full transcript, not per-cue -- cue
boundaries between a hypothesis and a reference rarely line up, so
per-cue comparison would just be noise.
"""
import re


def extract_text(srt_path: str) -> str:
    with open(srt_path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    blocks = content.strip().split("\n\n")
    lines = []
    for block in blocks:
        block_lines = block.split("\n")
        if len(block_lines) < 3:
            continue
        text = " ".join(block_lines[2:])
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\{[^}]*\}", "", text)
        text = " ".join(text.split())
        if text:
            lines.append(text)

    return " ".join(lines)
