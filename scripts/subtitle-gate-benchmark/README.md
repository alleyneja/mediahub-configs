# Subtitle gate benchmark (2026-11-15)
Scripts behind docs/subtitle-quality-gate.md section 8. Read-only; nothing here writes to the library.
- `gen_variants.py GOOD.json OUT` makes 17 controlled corruptions of known-good subs (shifts, framerate scales, truncation, gap, step, drift).
- `measure_container.py JOBS.json OUT.jsonl [N]` runs INSIDE the bazarr container (7 x 480 s windows, ffsubsync; audio extracted once per video).
- `score3.py MEASUREMENTS.jsonl` scores the three-state rule (PASS / FAIL / UNVERIFIABLE). Expects `good_titles.json` beside it.
- `chosen_rule_v1.json` is the rule that came out. Measurements are not committed (library titles); regenerate with the scripts.
