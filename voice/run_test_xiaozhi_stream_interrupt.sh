#!/usr/bin/env bash
set -Eeuo pipefail

source ~/.bashrc
source /mnt/ssd/pc/venvs/voice/bin/activate

cd /mnt/ssd/pc/projects/voice

python3 /mnt/ssd/pc/projects/voice/test_xiaozhi_stream_interrupt.py \
  --alsa-input-device plughw:CARD=Rx,DEV=0 \
  --alsa-input-channels 2 \
  --input-sample-rate 48000 \
  --stt-sample-rate 16000 \
  --stt-model paraformer-realtime-v2 \
  --qwen-model qwen-plus \
  --enable-search \
  --speaker-name "REDMI Speaker 2-6002" \
  --max-sentence-silence 700 \
  --question-settle-time 0.55 \
  --question-timeout 4.0 \
  --temperature 0 \
  --max-qwen-tokens 260 \
  --max-answer-sentences 4 \
  --loop-guide \
  "$@"
