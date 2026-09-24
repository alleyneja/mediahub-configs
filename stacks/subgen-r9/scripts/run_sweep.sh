#!/bin/bash
# Decoding-settings sweep: one setting changed at a time from the current
# baseline (large-v3, VAD off, condition_on_previous_text=False,
# hallucination_silence_threshold=2.0, default beam_size), run against all
# 7 test episodes, scored with score_subtitles.py. Baseline itself is not
# re-run -- today's single_pass.srt / single_pass_baseline.srt already are it.
#
# Runs a throwaway side container per trial on port 9001 (production
# `subgen` on 9000 stays untouched; Bazarr's whisperai provider is
# currently disabled so there's no live traffic to contend with).
set -uo pipefail

OUT=/home/jay/mediahub-cleanup-testdata/sweep
MODELS_VOL=/srv/docker/subgen/models
R9=192.168.0.22
PORT=9001
HOST_IP=100.121.244.45

# trial_name -> extra docker run -e flags (baseline SUBGEN_KWARGS/model implicit)
declare -A TRIAL_KWARGS=(
  [vad_on]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 2.0, 'vad_filter': True}"
  [halluc_low]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 1.0}"
  [halluc_high]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 4.0}"
  [condition_true]="{'condition_on_previous_text': True, 'hallucination_silence_threshold': 2.0}"
  [beam10]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 2.0, 'beam_size': 10}"
  [beam1]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 2.0, 'beam_size': 1}"
  [turbo]="{'condition_on_previous_text': False, 'hallucination_silence_threshold': 2.0}"
)
declare -A TRIAL_MODEL=(
  [vad_on]="large-v3"
  [halluc_low]="large-v3"
  [halluc_high]="large-v3"
  [condition_true]="large-v3"
  [beam10]="large-v3"
  [beam1]="large-v3"
  [turbo]="large-v3-turbo"
)
TRIAL_ORDER="vad_on halluc_low halluc_high condition_true beam10 beam1 turbo"

# episode -> "task|language|source_file"
declare -A EPISODES=(
  [s23e24]="transcribe|ja|/mnt/media/tv/One Piece/Season 23/One Piece - S23E24 - One Do-or-Die Second - Gaban vs. the Knights of God.mkv"
  [op-s23e20]="translate|ja|/home/jay/mediahub-cleanup-testdata/round2/op-s23e20/audio_ja.aac"
  [frieren-s02e03]="translate|ja|/home/jay/mediahub-cleanup-testdata/round2/frieren-s02e03/audio_ja.eac3"
  [csm-s01e11]="translate|ja|/home/jay/mediahub-cleanup-testdata/round2/csm-s01e11/audio_ja.aac"
  [ehc-s01e01]="transcribe|en|/mnt/media/tv/Everybody Hates Chris/Season 1/Everybody Hates Chris - S01E01 - Everybody Hates the Pilot.mkv"
  [bobs-s10e06]="transcribe|en|/mnt/media/tv/Bob's Burgers/Season 10/Bob's Burgers - S10E06 - The Hawkening - Look Who's Hawking Now!.mkv"
  [dexter-s02e08]="transcribe|en|/mnt/media/tv/Dexter/Season 2/Dexter - S02E08 - Morning Comes.mkv"
)
EPISODE_ORDER="s23e24 op-s23e20 frieren-s02e03 csm-s01e11 ehc-s01e01 bobs-s10e06 dexter-s02e08"

deploy_trial() {
  local trial="$1"
  local kwargs="${TRIAL_KWARGS[$trial]}"
  local model="${TRIAL_MODEL[$trial]}"
  ssh "$R9" "docker rm -f subgen-sweep 2>/dev/null; docker run -d --name subgen-sweep \
    --runtime nvidia \
    -e PUID=1000 -e PGID=1000 \
    -e WHISPER_MODEL=$model \
    -e TRANSCRIBE_DEVICE=cuda \
    -e COMPUTE_TYPE=float16 \
    -e CONCURRENT_TRANSCRIPTIONS=1 \
    -e CLEAR_VRAM_ON_COMPLETE=true \
    -e \"SUBGEN_KWARGS=$kwargs\" \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    -e TZ=America/Chicago \
    -v $MODELS_VOL:/subgen/models \
    -p $HOST_IP:$PORT:9000 \
    mccloud/subgen:2026.07.3" >/dev/null
}

wait_healthy() {
  for i in $(seq 1 30); do
    if curl -s -m 3 "http://$HOST_IP:$PORT/status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "  !! container never became healthy" >&2
  return 1
}

run_episode() {
  local trial="$1" episode="$2"
  IFS='|' read -r task lang src <<< "${EPISODES[$episode]}"
  local out="$OUT/$trial/$episode.srt"
  mkdir -p "$OUT/$trial"
  echo "  [$(date +%H:%M:%S)] $episode ($task/$lang)..."
  curl -s -X POST "http://$HOST_IP:$PORT/asr?task=$task&language=$lang&output=srt&encode=true" \
    -F "audio_file=@$src" \
    --max-time 3600 \
    -o "$out"
  local n
  n=$(grep -c '\-\->' "$out" 2>/dev/null || echo 0)
  echo "  [$(date +%H:%M:%S)] $episode done: $n cues"
}

run_sweep() {
  for trial in $TRIAL_ORDER; do
    echo "=== [$(date +%H:%M:%S)] TRIAL: $trial (model=${TRIAL_MODEL[$trial]}, kwargs=${TRIAL_KWARGS[$trial]}) ==="
    deploy_trial "$trial"
    if ! wait_healthy; then
      echo "=== SKIPPING $trial: container did not come up ==="
      continue
    fi
    for episode in $EPISODE_ORDER; do
      run_episode "$trial" "$episode"
    done
    ssh "$R9" "docker rm -f subgen-sweep" >/dev/null
    echo "=== [$(date +%H:%M:%S)] TRIAL $trial COMPLETE ==="
  done
  echo "=== SWEEP COMPLETE ==="
}

# Only auto-run when executed directly (./run_sweep.sh), not when sourced
# for its functions (e.g. to test deploy_trial in isolation).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  run_sweep
fi
