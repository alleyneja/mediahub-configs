# SubGen Multi-Pass Alignment/Voting for Last-Resort Subtitles

## Context

SubGen (`stacks/subgen-r9/`, `mccloud/subgen:2026.07.3`) is Bazarr's last-resort
subtitle provider on mediahub-r9's GPU — it only runs for episodes/movies no
database provider has ever indexed. It replaced `whisper-r9` on 2026-09-22
because that container's blunt VAD handling collapsed long speech spans into
single giant caption lines, silently dropping dialogue.

Today, `asr_task_worker()` in `subgen.py` (Bazarr's `/asr` request path) does a
single `model.transcribe()` call per episode. Since this is scraping the
bottom of the barrel for sources with no provider subs at all, a single pass
is the wrong place to economize: there is no real-time constraint on this
pipeline (new provider-less content arrives rarely, and even then a provider
sub usually shows up within days), so we're accepting a 2-3x+ runtime cost per
episode to run multiple transcription passes and reconcile them into one
higher-quality result, instead of a single pass plus simple gap-fill.

Two design threads converge here:
1. **Multi-pass + voting** (this doc): how the reconciliation itself works.
2. **Bazarr-side QA locking** (separate, not yet designed): preventing Bazarr
   from re-triggering generation on an episode while QA of the previous
   result is still in flight. Whatever that lock design turns out to be, it
   needs to hold for the full duration of this multi-pass process, not a
   single pass — noted here as a dependency, not solved by this doc.

## Decision: fork SubGen directly

Considered three integration points: (a) a proxy service in front of SubGen's
`/asr` endpoint, (b) forking SubGen's own source, (c) a fully standalone
script bypassing SubGen. Chose **(b), fork SubGen directly** — accepting the
ongoing cost of maintaining a custom build and re-applying the fork's changes
on upstream SubGen updates, in exchange for keeping this to one service
instead of adding a new one, and direct access to the `WhisperResult` object
mid-pipeline rather than only its serialized SRT/JSON output.

## Hook point

`asr_task_worker()`, `subgen.py:1050`. Replace the single call at line 1089:

```python
result = model.transcribe(task=task, language=language, **args, verbose=None)
```

with a multi-pass-and-reconcile step that produces one `WhisperResult`, then
let everything downstream of it (`apply_timestamp_offset`, `appendLine`,
output formatting) run unchanged.

Scope: this only covers the `/asr` path (what Bazarr's WhisperAI provider
protocol calls). The Plex/Jellyfin webhook-triggered path (`gen_subtitles()`,
a separate function used for direct library scans, not Bazarr) is explicitly
out of scope and stays single-pass.

## Pass generation

`SUBGEN_QA_PASSES` env var, default `3`. Each pass uses a different
`temperature` so the passes are genuinely different hypotheses rather than
near-identical repeats — faster-whisper's decoding is largely deterministic
at a fixed temperature, and only its own fallback ladder introduces sampling
variance, which by default only kicks in on segments Whisper itself already
flagged as low-confidence. Explicitly varying temperature per pass gets
diversity everywhere, not just on already-flagged segments:

- Pass 1: `temperature=0.0` (today's effective behavior)
- Pass 2: `temperature=0.3`
- Pass 3: `temperature=0.6`

If `SUBGEN_QA_PASSES` is increased beyond 3, extend the ladder within
Whisper's native fallback range (e.g. up to `1.0`) rather than introducing
unrelated parameters.

## Alignment: time-window clustering, not text sequence alignment

All N passes transcribe the same audio and each returns word-level timing
(`stable_whisper.WordTiming`: `.start`, `.end`, `.word`, `.probability`) via
`result.segments[i].words`. Because we have a synchronized timeline across
passes — something classic ASR-combination techniques (e.g. ROVER) don't
normally have — alignment is a single linear pass over time, not multi-way
sequence alignment:

1. Pool every word from every pass into one list of
   `(start, end, text, probability, pass_id)`, sorted by `start`.
2. Walk the sorted list, greedily grouping into slots: a word joins the
   current slot if its interval overlaps the slot's running interval by
   ≥50% IoU (env var `SUBGEN_QA_OVERLAP_THRESHOLD`, default `0.5`);
   otherwise it opens a new slot.

Each slot ends up with 0-N candidate words (one per pass that produced
something in that time region).

## Voting per slot

1. Normalize each candidate's text (lowercase, strip punctuation) for
   comparison only.
2. Majority vote among candidates. Ties (including "all N differ") are
   broken by the single candidate with the highest `.probability`.
3. The winning candidate's *original* (non-normalized) text and its own
   timestamps are what get kept — normalization is only for comparison.

**Quorum policy for thin slots:** a slot with a word from only 1 of N passes
(others show silence there) is **dropped** unless at least 2 of N passes
agree something is present. Rationale: this pipeline is explicitly
last-resort/bottom-of-barrel source material, and Whisper's known failure
mode on bad audio is hallucinating plausible-sounding dialogue over
near-silence. A single dissenting pass is more likely to be one hallucination
than a genuine catch the other two missed; confidently wrong text is worse
for a viewer than a dropped line.

## Reassembly

Feed the winning words back into `stable_whisper.WhisperResult()` as a flat
word list (its constructor accepts `list[list[dict]]` and builds segments
from it directly — see `stable_ts/result.py:965-996`), then call
`.regroup(custom_regroup)` using the same `CUSTOM_REGROUP` value already
configured in `stacks/subgen-r9/docker-compose.yml`, instead of writing new
segment-boundary logic. The result is a normal `WhisperResult`, so
`apply_timestamp_offset`, `appendLine`, and `to_srt_vtt()` all keep working
unmodified.

## Error handling

- A pass that throws (CUDA error, decode failure, etc.) is logged and
  skipped; voting proceeds with whatever passes succeeded, provided **at
  least 2 succeeded**.
- If fewer than 2 passes succeed, fall back to the single successful pass's
  raw (unreconciled) result rather than failing the request outright — a
  last-resort provider failing completely is worse than skipping QA for one
  episode.
- **To verify during rollout, not assumed:** Bazarr's configured WhisperAI
  provider timeout must comfortably exceed the new 2-3x+ per-episode runtime,
  or Bazarr will give up on the request before SubGen finishes.

## Concurrency (resolves the open side-question from prior discussion)

SubGen already serializes all transcription work: `concurrent_transcriptions`
worker threads (currently `1`, from `CONCURRENT_TRANSCRIPTIONS=1` in the
compose file) pull from a single `task_queue`
(`subgen.py:354` `transcription_worker`, `subgen.py:423`). Bazarr firing
multiple requests at once already just queues them — there is no GPU
contention to design around. No change needed here; multi-pass just makes
each already-serialized job take 2-3x+ longer.

## Testing plan

Fixed test case: **One Piece S23E24** ("One Do-or-Die Second — Gaban vs. the
Knights of God, Elbaph"), continuing the line-by-line audit methodology from
the 2026-09-22 quality investigation. Pull three subtitle tracks for the same
episode:

1. **Embedded subs** — ground truth / "perfect" baseline.
2. **Current single-pass SubGen output** — today's behavior, no voting.
3. **New multi-pass + voting output** — this design.

Compare line-by-line against the embedded baseline to confirm voting actually
moves output quality toward ground truth, not just "looks plausible."

## Config summary (all env vars, nothing hardcoded)

| Var | Default | Purpose |
|---|---|---|
| `SUBGEN_QA_PASSES` | `3` | Number of transcription passes per episode |
| `SUBGEN_QA_OVERLAP_THRESHOLD` | `0.5` | IoU threshold for clustering words into the same time slot |

Per-pass temperature ladder is derived from `SUBGEN_QA_PASSES` in code (see
Pass Generation), not a separate env var.
