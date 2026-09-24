# SubGen Multi-Pass Alignment/Voting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SubGen's single Whisper transcription pass (Bazarr's last-resort subtitle provider) with 3+ passes reconciled via time-window clustering and majority voting, and prove it beats today's single-pass output on the One Piece S23E24 test case.

**Architecture:** Fork SubGen's source (vendored from the exact running `mccloud/subgen:2026.07.3` container, not upstream `main`) into `stacks/subgen-r9/src/`, build a custom image from it. Add a new dependency-free module (`qa_voting.py`) that does time-window clustering + majority vote on flat word lists — no `stable_whisper` import, fully unit-testable in isolation. Wire it into `asr_task_worker()` (the function Bazarr's `/asr` calls) by replacing its single `model.transcribe()` call with a loop over an increasing-temperature ladder, feeding all passes' words into `qa_voting.reconcile_passes()`, then rebuilding one `stable_whisper.WhisperResult` from the winning words and calling its existing `.regroup()` to reform subtitle cues.

**Tech Stack:** Python 3 (stdlib only for `qa_voting.py`; `stable_whisper`/`faster-whisper` for the vendored `subgen.py`, unchanged versions), Docker Compose v5 on mediahub-r9 (192.168.0.22, SSH key + NOPASSWD sudo already configured), `unittest` for `qa_voting.py` (no `pytest` — not installed on this session's host and there's no working `pip` to add it, so stdlib-only tests keep the dev loop dependency-free).

**Spec:** `docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md`

## Global Constraints

- `SUBGEN_QA_PASSES` default `3`; per-pass temperature ladder spans `[0.0, 1.0]` (e.g. 3 passes → `[0.0, 0.3, 0.6]`).
- `SUBGEN_QA_OVERLAP_THRESHOLD` default `0.5` (IoU) for clustering words into the same time slot.
- Quorum: a slot with contributions from fewer than 2 passes is dropped entirely (hallucination suppression).
- If fewer than 2 of the configured passes succeed, fall back to the single successful pass's raw (unreconciled) result; if zero succeed, raise rather than returning empty/corrupt output.
- Scope is `asr_task_worker()` / the `/asr` endpoint only (what Bazarr calls). `gen_subtitles()` (the Plex/Jellyfin webhook path) is untouched.
- Reassembly goes through `stable_whisper.WhisperResult([...]).regroup(custom_regroup)`, reusing the existing `CUSTOM_REGROUP` config — no new segment-boundary logic.
- The container's `launcher.py` re-downloads `subgen.py`/`language_code.py` from GitHub only if the file is missing or `UPDATE` is truthy. Since our Dockerfile bakes the vendored files into the image, this is safe by default — but the compose file must set `UPDATE=False` explicitly rather than relying on the var's absence, so a future env change can't silently clobber the fork with upstream `main`.

## Review Focus

- Two passes producing words that overlap partially but at different granularity (one pass's single word spans what another pass split into two shorter words) — clustering must group by real interval overlap, not exact boundary match. Covered in Task 3.
- A slot where all contributing candidates are already-normalized-identical (agreement) must still pick a real timestamp/probability, not silently return a malformed winner. Covered in Task 3.
- A 3-way (or N-way) split with no majority must fall back to the single highest-probability candidate, not throw or drop the slot. Covered in Task 3.
- Non-ASCII/Japanese punctuation and casing must be normalized for vote *comparison* without corrupting the *output* text of the winner (this is Japanese-audio content). Covered in Task 3.
- All `SUBGEN_QA_PASSES` passes failing (e.g. transient CUDA error) must raise a clear error rather than returning an empty SRT that looks like "no dialogue in this episode." Covered in Task 4.

---

## Task 1: Capture baseline artifacts for One Piece S23E24 (before touching any code)

Captures the "before" state so nothing downstream contaminates it. The test file is `/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv`, which has an embedded Japanese subtitle track (stream index 2, `subrip`) confirmed via `ffprobe`. SubGen is reachable directly from this session at `http://100.121.244.45:9000` (confirmed: `{"version":"Subgen 2026.07.3, stable-ts 2.19.1, faster-whisper 1.2.1 (Docker)"}`).

**Files:**
- Create: `/home/jay/mediahub-cleanup-testdata/s23e24/embedded_japanese.srt` (ground truth baseline)
- Create: `/home/jay/mediahub-cleanup-testdata/s23e24/single_pass_baseline.srt` (today's SubGen output, captured before any fork changes)
- Create: `/home/jay/mediahub-cleanup-testdata/s23e24/README.md` (records exactly how each file was produced, for anyone comparing later)

**Interfaces:**
- Produces: two `.srt` files later tasks compare against — `embedded_japanese.srt` (ground truth) and `single_pass_baseline.srt` (pre-fork control).

- [ ] **Step 1: Create the test data directory**

```bash
mkdir -p /home/jay/mediahub-cleanup-testdata/s23e24
```

- [ ] **Step 2: Extract the embedded Japanese subtitle track as ground truth**

```bash
ffmpeg -y -i "/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv" \
  -map 0:2 \
  /home/jay/mediahub-cleanup-testdata/s23e24/embedded_japanese.srt
```

- [ ] **Step 3: Verify the extraction produced real content**

```bash
wc -l /home/jay/mediahub-cleanup-testdata/s23e24/embedded_japanese.srt
head -8 /home/jay/mediahub-cleanup-testdata/s23e24/embedded_japanese.srt
```

Expected: more than a handful of lines, and `head` shows numbered cues with `-->` timestamp lines and Japanese text — not an empty or error file.

- [ ] **Step 4: Capture today's single-pass SubGen output (pre-fork control), in the background since it blocks for the full episode length**

```bash
curl -s -X POST "http://100.121.244.45:9000/asr?task=transcribe&language=ja&output=srt&encode=true" \
  -F "audio_file=@/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv;type=video/x-matroska" \
  --max-time 1800 \
  -o /home/jay/mediahub-cleanup-testdata/s23e24/single_pass_baseline.srt &
disown
```

- [ ] **Step 5: Wait for it to finish, then verify**

```bash
# poll every 30s: `jobs` shows nothing once curl exits
wc -l /home/jay/mediahub-cleanup-testdata/s23e24/single_pass_baseline.srt
head -8 /home/jay/mediahub-cleanup-testdata/s23e24/single_pass_baseline.srt
```

Expected: real SRT content, comparable line count to the embedded track (same 23-minute episode).

- [ ] **Step 6: Write the README documenting provenance**

```bash
cat > /home/jay/mediahub-cleanup-testdata/s23e24/README.md << 'EOF'
# S23E24 multi-pass voting test artifacts

Source: One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of
God (embedded Japanese audio + subtitle track).

- `embedded_japanese.srt` — ground truth. Extracted via `ffmpeg -map 0:2`
  from the mkv's embedded `subrip` stream (language tag `jpn`).
- `single_pass_baseline.srt` — today's SubGen behavior (single pass,
  mccloud/subgen:2026.07.3, unmodified), captured via a direct POST to
  `/asr?task=transcribe&language=ja&output=srt&encode=true` BEFORE any fork
  changes were built or deployed. This is the "before" control.
- `multi_pass_voted.srt` — added in a later task: the new multi-pass +
  voting output, same endpoint, same request, after the fork is deployed.

See docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md for
the design this is testing.
EOF
```

- [ ] **Step 7: Commit the baseline artifacts**

```bash
cd /home/jay/mediahub-configs
git add -A /home/jay/mediahub-cleanup-testdata/s23e24 2>/dev/null || true
```

Note: `/home/jay/mediahub-cleanup-testdata` is outside the `mediahub-configs` repo on purpose (test fixtures/generated subtitle dumps don't belong in an infra-config repo). No git commit needed for this step — just confirm the three files exist:

```bash
ls -la /home/jay/mediahub-cleanup-testdata/s23e24/
```

Expected: `embedded_japanese.srt`, `single_pass_baseline.srt`, `README.md`, each with real content.

---

## Task 2: Vendor SubGen source and stand up the fork's build pipeline (no logic changes yet)

Proves the fork/build/deploy pipeline works and is behavior-identical to the pinned image, before any new logic is added — so a later regression is provably in the new logic, not the build switch.

**Files:**
- Create: `stacks/subgen-r9/src/subgen.py` (vendored, unmodified, extracted from the running container)
- Create: `stacks/subgen-r9/src/launcher.py` (vendored, unmodified)
- Create: `stacks/subgen-r9/src/language_code.py` (vendored, unmodified)
- Create: `stacks/subgen-r9/src/requirements.txt` (vendored, unmodified)
- Create: `stacks/subgen-r9/src/entrypoint.sh` (vendored, unmodified)
- Create: `stacks/subgen-r9/src/Dockerfile` (new, adapted from upstream's build recipe)
- Modify: `stacks/subgen-r9/docker-compose.yml` (swap `image:` for `build:`)

**Interfaces:**
- Produces: a locally-built `subgen` image on r9, deployed and serving `/status` identically to the pinned upstream image.

- [ ] **Step 1: Extract the exact running app files from the live container on r9**

```bash
ssh 192.168.0.22 'mkdir -p /tmp/subgen-vendor && \
  docker cp subgen:/subgen/subgen.py /tmp/subgen-vendor/subgen.py && \
  docker cp subgen:/subgen/launcher.py /tmp/subgen-vendor/launcher.py && \
  docker cp subgen:/subgen/language_code.py /tmp/subgen-vendor/language_code.py && \
  docker cp subgen:/requirements.txt /tmp/subgen-vendor/requirements.txt && \
  docker cp subgen:/entrypoint.sh /tmp/subgen-vendor/entrypoint.sh'
```

- [ ] **Step 2: Copy the extracted files into the repo**

```bash
mkdir -p /home/jay/mediahub-configs/stacks/subgen-r9/src
scp 192.168.0.22:/tmp/subgen-vendor/subgen.py \
    192.168.0.22:/tmp/subgen-vendor/launcher.py \
    192.168.0.22:/tmp/subgen-vendor/language_code.py \
    192.168.0.22:/tmp/subgen-vendor/requirements.txt \
    192.168.0.22:/tmp/subgen-vendor/entrypoint.sh \
    /home/jay/mediahub-configs/stacks/subgen-r9/src/
```

- [ ] **Step 3: Verify what was extracted matches what's actually running**

```bash
wc -l /home/jay/mediahub-configs/stacks/subgen-r9/src/subgen.py
grep -n "^def asr_task_worker" /home/jay/mediahub-configs/stacks/subgen-r9/src/subgen.py
```

Expected: a single-digit-thousands line count (upstream `main` was ~2500+ lines at clone time) and the `asr_task_worker` definition present.

- [ ] **Step 4: Write the fork's Dockerfile**

```dockerfile
FROM nvidia/cuda:12.8.1-base-ubuntu22.04

# Apt packages — own layer so pip changes don't re-run apt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg python3 python3-pip curl gosu tzdata

# Torch — large and rarely changes; own layer so requirements.txt changes don't bust it
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -U torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# App dependencies — only rebuilds when requirements.txt changes
COPY requirements.txt /
RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install -U -r /requirements.txt \
    && apt-get purge -y --auto-remove python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /subgen

# App files last — changes here don't bust the layers above.
# qa_voting.py (added in Task 4) is our own module, not from upstream.
COPY launcher.py subgen.py language_code.py qa_voting.py /subgen/

RUN mkdir -p /cache && chmod 777 /cache

ENV XDG_CACHE_HOME=/cache \
    HF_HOME=/cache/huggingface \
    MPLCONFIGDIR=/cache/matplotlib \
    PYTHONUNBUFFERED=1

COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "launcher.py"]
```

Save to `/home/jay/mediahub-configs/stacks/subgen-r9/src/Dockerfile`.

Note: this Dockerfile's `COPY launcher.py subgen.py language_code.py qa_voting.py` line references `qa_voting.py`, which doesn't exist until Task 4. That's fine for now — Step 6 of this task builds and deploys with a temporary placeholder so the pipeline itself gets proven; Task 4 replaces the placeholder with the real module.

- [ ] **Step 5: Create a temporary placeholder `qa_voting.py` so this task's build succeeds standalone**

```bash
cat > /home/jay/mediahub-configs/stacks/subgen-r9/src/qa_voting.py << 'EOF'
# Placeholder — replaced with real logic in Task 4 of the implementation
# plan. Exists here only so Task 2's build/deploy smoke test can run
# before the voting logic is written.
EOF
```

- [ ] **Step 6: Point the compose file at the local build instead of the pinned image**

In `/home/jay/mediahub-configs/stacks/subgen-r9/docker-compose.yml`, replace:

```yaml
    image: mccloud/subgen:2026.07.3
```

with:

```yaml
    build:
      context: ./src
      dockerfile: Dockerfile
```

Also add a comment above `services:` noting the fork, e.g.:

```yaml
# Forked 2026-09-23 to add multi-pass transcription + alignment/voting QA
# (see docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md).
# Source vendored from the exact running mccloud/subgen:2026.07.3 image,
# not upstream main, so this build is behavior-identical to production
# until the QA logic is layered on in a later change.
```

- [ ] **Step 7: Commit the vendored source and compose change**

```bash
cd /home/jay/mediahub-configs
git add stacks/subgen-r9/src/ stacks/subgen-r9/docker-compose.yml
git commit -m "$(cat <<'EOF'
Fork SubGen: vendor source, switch to local build (no logic changes yet)

Vendored from the exact running mccloud/subgen:2026.07.3 container so the
fork starts behavior-identical to production. This is step 1 of the
multi-pass voting work (docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md)
— qa_voting.py is a placeholder here, wired up for real in a later commit.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Deploy the vendored build to r9**

```bash
rsync -av /home/jay/mediahub-configs/stacks/subgen-r9/ 192.168.0.22:~/mediahub-configs/stacks/subgen-r9/
ssh 192.168.0.22 'cd ~/mediahub-configs/stacks/subgen-r9 && docker compose build && docker compose up -d'
```

- [ ] **Step 9: Smoke-test: confirm the rebuilt container serves identically to before**

```bash
sleep 5
curl -s -m 5 "http://100.121.244.45:9000/status"
ssh 192.168.0.22 'docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}" | grep subgen'
```

Expected: `/status` still returns `{"version":"Subgen 2026.07.3, stable-ts 2.19.1, faster-whisper 1.2.1 (Docker)"}` (same versions — `requirements.txt` is unchanged), and `docker ps` shows the `subgen` container `Up`, now built from a local image (not pulled from Docker Hub).

---

## Task 3: Implement `qa_voting.py` — time-window clustering, quorum, and majority vote (TDD)

Pure-Python, zero external dependencies (no `stable_whisper` import), so it's testable with plain `unittest` — no model, no Docker, no GPU needed for this task.

**Files:**
- Create: `stacks/subgen-r9/src/qa_voting.py` (replaces the Task 2 placeholder)
- Create: `stacks/subgen-r9/src/test_qa_voting.py`

**Interfaces:**
- Produces (consumed by Task 4's `subgen.py` changes):
  - `reconcile_passes(passes: list[list[dict]], overlap_threshold: float = 0.5) -> list[dict]` — `passes` is a list of N passes, each a list of word dicts `{"start": float, "end": float, "word": str, "probability": float}` already sorted by `start` within each pass. Returns the winning words (same dict shape, no `pass_id`), sorted by `start`.

- [ ] **Step 1: Write failing tests for the clustering helper**

```python
# stacks/subgen-r9/src/test_qa_voting.py
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail (module doesn't exist yet)**

```bash
cd /home/jay/mediahub-configs/stacks/subgen-r9/src
python3 -m unittest test_qa_voting -v
```

Expected: `ModuleNotFoundError: No module named 'qa_voting'` (or import errors for the not-yet-defined names) — confirms the tests actually exercise real code once it exists.

- [ ] **Step 3: Implement `_normalize` and `_cluster_words_into_slots`**

```python
# stacks/subgen-r9/src/qa_voting.py
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
```

- [ ] **Step 4: Run the tests again to verify they pass**

```bash
cd /home/jay/mediahub-configs/stacks/subgen-r9/src
python3 -m unittest test_qa_voting -v
```

Expected: all `TestNormalize` and `TestClustering` cases `PASS`.

- [ ] **Step 5: Commit**

```bash
cd /home/jay/mediahub-configs
git add stacks/subgen-r9/src/qa_voting.py stacks/subgen-r9/src/test_qa_voting.py
git commit -m "$(cat <<'EOF'
Add qa_voting time-window clustering (word normalization + slot grouping)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Write failing tests for voting, quorum, and the public `reconcile_passes` entry point**

```python
# add to stacks/subgen-r9/src/test_qa_voting.py

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
```

- [ ] **Step 7: Run tests to verify the new ones fail**

```bash
cd /home/jay/mediahub-configs/stacks/subgen-r9/src
python3 -m unittest test_qa_voting -v
```

Expected: `TestVoteSlot` and `TestReconcilePasses` cases fail with `AttributeError`/`NameError` (`_vote_slot`/`reconcile_passes` not defined yet).

- [ ] **Step 8: Implement `_vote_slot` and `reconcile_passes`**

```python
# append to stacks/subgen-r9/src/qa_voting.py

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
```

- [ ] **Step 9: Run all tests to verify they pass**

```bash
cd /home/jay/mediahub-configs/stacks/subgen-r9/src
python3 -m unittest test_qa_voting -v
```

Expected: every test in the file `PASS` (clustering + voting + reconcile_passes, 11 tests total).

- [ ] **Step 10: Commit**

```bash
cd /home/jay/mediahub-configs
git add stacks/subgen-r9/src/qa_voting.py stacks/subgen-r9/src/test_qa_voting.py
git commit -m "$(cat <<'EOF'
Add qa_voting quorum + majority vote + reconcile_passes entry point

Completes the pure-logic half of multi-pass QA (see design spec). No
stable_whisper dependency, so this is unit-tested with stdlib unittest
alone; wiring it into subgen.py's actual transcription loop is next.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire multi-pass transcription + reconciliation into `subgen.py`, deploy

**Files:**
- Modify: `stacks/subgen-r9/src/subgen.py` (env var parsing near the top; `asr_task_worker` around what was originally line 1050-1097)
- Modify: `stacks/subgen-r9/docker-compose.yml` (add `SUBGEN_QA_PASSES`, `SUBGEN_QA_OVERLAP_THRESHOLD`, `UPDATE=False`)

**Interfaces:**
- Consumes: `qa_voting.reconcile_passes(passes: list[list[dict]], overlap_threshold: float) -> list[dict]` from Task 3.
- Produces: the actual behavior Task 5 tests against.

- [ ] **Step 1: Find the exact current env-var-parsing block for `custom_regroup`**

```bash
grep -n "custom_regroup = os.getenv" /home/jay/mediahub-configs/stacks/subgen-r9/src/subgen.py
```

Expected: one match, e.g. `custom_regroup = os.getenv('CUSTOM_REGROUP', 'cm_sl=84_sl=42++++++1')`. Note the line number — add the new env vars directly below it.

- [ ] **Step 2: Add the new env vars and a temperature-ladder helper right after that line**

Insert immediately after the `custom_regroup = os.getenv(...)` line:

```python
qa_passes = int(os.getenv('SUBGEN_QA_PASSES', '3'))
qa_overlap_threshold = float(os.getenv('SUBGEN_QA_OVERLAP_THRESHOLD', '0.5'))


def _temperature_ladder(passes: int) -> list:
    """Per-pass decoding temperature, spread across Whisper's native
    fallback range [0.0, 1.0]. 3 passes (the default) gives [0.0, 0.3, 0.6].
    Deliberately varying temperature per pass (rather than repeating
    identical calls) is what makes the passes different hypotheses to vote
    on -- faster-whisper's decoding is otherwise largely deterministic.
    """
    if passes <= 1:
        return [0.0]
    step = min(0.3, 1.0 / (passes - 1))
    return [round(min(i * step, 1.0), 2) for i in range(passes)]
```

- [ ] **Step 3: Add the `qa_voting` import alongside the existing `stable_whisper` import**

```bash
grep -n "^import stable_whisper" /home/jay/mediahub-configs/stacks/subgen-r9/src/subgen.py
```

Add directly below that line:

```python
import qa_voting
```

(`launcher.py` runs `subgen.py` as `python3 -u subgen.py` from `/subgen`, its own directory, which Python puts on `sys.path[0]` automatically — no path manipulation needed for `qa_voting.py` to resolve, since the Dockerfile `COPY`s it into the same `/subgen/` directory.)

- [ ] **Step 4: Replace the single-pass transcribe call in `asr_task_worker` with the multi-pass loop + reconciliation**

Find the current block (originally around line 1080-1097):

```python
        if custom_regroup and custom_regroup.lower() != 'default':
            args['regroup'] = custom_regroup

        args.update(kwargs)

        # Detect audio start_time offset from source file (if accessible)
        audio_offset = get_audio_start_time(video_file) if video_file else 0.0

        # Perform transcription
        result = model.transcribe(task=task, language=language, **args, verbose=None)

        # Apply audio start_time offset to compensate for container timing
        # Whisper ignores silence padding (adelay) from Bazarr, so timestamps
        # are relative to audio stream start, not container start
        if audio_offset > 0:
            apply_timestamp_offset(result, audio_offset)

        appendLine(result)
```

Replace with:

```python
        if custom_regroup and custom_regroup.lower() != 'default':
            args['regroup'] = custom_regroup

        args.update(kwargs)

        # Detect audio start_time offset from source file (if accessible)
        audio_offset = get_audio_start_time(video_file) if video_file else 0.0

        # Multi-pass QA: run qa_passes transcription passes with an
        # increasing temperature ladder, then reconcile via time-window
        # clustering + majority vote instead of trusting a single pass.
        # See docs/superpowers/specs/2026-09-23-subgen-multipass-voting-design.md
        pass_results = []
        for i, temperature in enumerate(_temperature_ladder(qa_passes)):
            try:
                pass_args = dict(args)
                pass_args['temperature'] = temperature
                pass_args['progress_callback'] = ProgressHandler(f"{display_name} (pass {i + 1}/{qa_passes})")
                pass_results.append(model.transcribe(task=task, language=language, **pass_args, verbose=None))
            except Exception as e:
                logging.error(f"QA pass {i + 1}/{qa_passes} at temperature={temperature} failed (ID: {task_id}): {e}", exc_info=True)

        if len(pass_results) >= 2:
            passes_as_words = [
                [
                    {"start": word.start, "end": word.end, "word": word.word, "probability": word.probability}
                    for seg in pass_result.segments
                    for word in seg.words
                ]
                for pass_result in pass_results
            ]
            reconciled_words = qa_voting.reconcile_passes(passes_as_words, overlap_threshold=qa_overlap_threshold)
            result = stable_whisper.WhisperResult([reconciled_words])
            if custom_regroup and custom_regroup.lower() != 'default':
                result.regroup(custom_regroup)
        elif len(pass_results) == 1:
            logging.warning(f"Only 1 of {qa_passes} QA passes succeeded (ID: {task_id}); using it unreconciled")
            result = pass_results[0]
        else:
            raise RuntimeError(f"All {qa_passes} QA passes failed for ASR request (ID: {task_id})")

        # Apply audio start_time offset to compensate for container timing
        # Whisper ignores silence padding (adelay) from Bazarr, so timestamps
        # are relative to audio stream start, not container start
        if audio_offset > 0:
            apply_timestamp_offset(result, audio_offset)

        appendLine(result)
```

Note: `args['progress_callback']` was previously set once, before this block, via `args['progress_callback'] = ProgressHandler(display_name)` (still present a few lines earlier in the function). That original assignment is now dead code for the multi-pass path specifically because `pass_args` (a fresh copy) overwrites `progress_callback` per pass with a pass-numbered label — leave the original line in place (it's harmless and `args` may still be read elsewhere), just know it's superseded here.

- [ ] **Step 5: Confirm Task 3 already overwrote Task 2's placeholder (no action needed, just verify)**

```bash
grep -n "Placeholder" /home/jay/mediahub-configs/stacks/subgen-r9/src/qa_voting.py
```

Expected: no match — Task 3's Step 3 wrote real code directly to this same path, so nothing to copy here.

- [ ] **Step 6: Add the new env vars to the compose file, plus explicit `UPDATE=False`**

In `/home/jay/mediahub-configs/stacks/subgen-r9/docker-compose.yml`, under the `subgen` service's `environment:` block, add:

```yaml
      - SUBGEN_QA_PASSES=3
      - SUBGEN_QA_OVERLAP_THRESHOLD=0.5
      # launcher.py re-downloads subgen.py from GitHub main if this is
      # truthy, which would silently overwrite our fork's multi-pass logic.
      # Explicit False, not just relying on the var being absent.
      - UPDATE=False
```

- [ ] **Step 7: Commit**

```bash
cd /home/jay/mediahub-configs
git add stacks/subgen-r9/src/subgen.py stacks/subgen-r9/src/qa_voting.py stacks/subgen-r9/docker-compose.yml
git commit -m "$(cat <<'EOF'
Wire multi-pass transcription + qa_voting reconciliation into asr_task_worker

Replaces the single model.transcribe() call with a temperature-ladder loop
over SUBGEN_QA_PASSES passes, reconciled via qa_voting.reconcile_passes()
and reassembled through stable_whisper's existing regroup(). Falls back to
the single successful pass if fewer than 2 of N passes succeed; raises if
all fail. Explicit UPDATE=False guards against launcher.py's GitHub
auto-download clobbering the fork.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Deploy to r9**

```bash
rsync -av /home/jay/mediahub-configs/stacks/subgen-r9/ 192.168.0.22:~/mediahub-configs/stacks/subgen-r9/
ssh 192.168.0.22 'cd ~/mediahub-configs/stacks/subgen-r9 && docker compose build && docker compose up -d'
```

- [ ] **Step 9: Confirm the container starts cleanly and the new env vars are visible inside it**

```bash
sleep 5
curl -s -m 5 "http://100.121.244.45:9000/status"
ssh 192.168.0.22 'docker exec subgen printenv | grep -E "SUBGEN_QA|UPDATE"'
```

Expected: `/status` responds (container is up), and `printenv` shows `SUBGEN_QA_PASSES=3`, `SUBGEN_QA_OVERLAP_THRESHOLD=0.5`, `UPDATE=False`.

- [ ] **Step 10: Smoke-test with a short clip before committing to the full 23-minute episode**

Extract a 60-second sample so a broken pipeline fails fast instead of after 15 minutes:

```bash
ffmpeg -y -ss 60 -t 60 -i "/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv" \
  -c copy /home/jay/mediahub-cleanup-testdata/s23e24/sample_60s.mkv

curl -s -X POST "http://100.121.244.45:9000/asr?task=transcribe&language=ja&output=srt&encode=true" \
  -F "audio_file=@/home/jay/mediahub-cleanup-testdata/s23e24/sample_60s.mkv;type=video/x-matroska" \
  --max-time 300 \
  -o /home/jay/mediahub-cleanup-testdata/s23e24/sample_60s_multipass.srt

wc -l /home/jay/mediahub-cleanup-testdata/s23e24/sample_60s_multipass.srt
cat /home/jay/mediahub-cleanup-testdata/s23e24/sample_60s_multipass.srt
```

Expected: valid SRT output (numbered cues, `-->` timestamps, text), taking roughly 2-3x as long as a single-pass request of the same clip would (3 passes). Check the container logs for the per-pass progress lines to confirm 3 passes actually ran:

```bash
ssh 192.168.0.22 'docker logs --since 5m subgen 2>&1 | grep "pass [0-9]/3"'
```

Expected: log lines for `pass 1/3`, `pass 2/3`, `pass 3/3`.

---

## Task 5: Run the full S23E24 three-way comparison

**Files:**
- Create: `/home/jay/mediahub-cleanup-testdata/s23e24/multi_pass_voted.srt`

**Interfaces:**
- Consumes: `embedded_japanese.srt` and `single_pass_baseline.srt` from Task 1.

- [ ] **Step 1: Run the full episode through the deployed multi-pass pipeline**

```bash
curl -s -X POST "http://100.121.244.45:9000/asr?task=transcribe&language=ja&output=srt&encode=true" \
  -F "audio_file=@/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv;type=video/x-matroska" \
  --max-time 3600 \
  -o /home/jay/mediahub-cleanup-testdata/s23e24/multi_pass_voted.srt &
disown
```

- [ ] **Step 2: Wait for completion, then verify**

```bash
wc -l /home/jay/mediahub-cleanup-testdata/s23e24/multi_pass_voted.srt
head -8 /home/jay/mediahub-cleanup-testdata/s23e24/multi_pass_voted.srt
```

Expected: real SRT content, roughly comparable cue count to `single_pass_baseline.srt`.

- [ ] **Step 3: Confirm all 3 passes ran for this request (not a silent fallback to 1)**

```bash
ssh 192.168.0.22 'docker logs --since 1h subgen 2>&1 | grep "pass [0-9]/3" | tail -20'
```

Expected: log lines for all 3 passes on this request, no `"Only 1 of 3 QA passes succeeded"` warning and no `RuntimeError` for it.

- [ ] **Step 4: Lay all three files side by side for review**

```bash
echo "=== Embedded (ground truth) ===" 
sed -n '1,20p' /home/jay/mediahub-cleanup-testdata/s23e24/embedded_japanese.srt
echo
echo "=== Single-pass baseline (pre-fork) ==="
sed -n '1,20p' /home/jay/mediahub-cleanup-testdata/s23e24/single_pass_baseline.srt
echo
echo "=== Multi-pass voted (new) ==="
sed -n '1,20p' /home/jay/mediahub-cleanup-testdata/s23e24/multi_pass_voted.srt
```

This is the deliverable for judgment, not an automated pass/fail — the goal is a testable artifact for you to read line-by-line, same as the 2026-09-22 S23E24 audit methodology, and decide whether the voting actually moved output quality toward the embedded baseline versus the single-pass control.

- [ ] **Step 5: Update the README with a pointer to the finished comparison**

```bash
cat >> /home/jay/mediahub-cleanup-testdata/s23e24/README.md << 'EOF'

## Status

All three files captured as of $(date -I). multi_pass_voted.srt was
produced with SUBGEN_QA_PASSES=3, SUBGEN_QA_OVERLAP_THRESHOLD=0.5 against
the forked subgen-r9 build (docs/superpowers/plans/2026-09-23-subgen-multipass-voting.md).
Quality judgment against the embedded baseline: pending manual review.
EOF
```

(Replace `$(date -I)` with the actual date before running, if not using bash's command substitution directly in the heredoc.)
