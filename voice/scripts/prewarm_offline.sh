#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="$ROOT/voice:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="${VOICE_RUNTIME_LIB:-$ROOT-runtime/lib}:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"
export HF_HOME="$ROOT/voice/hf_cache"
export HUGGINGFACE_HUB_CACHE="$HF_HOME/hub"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
exec "$ROOT/voice/.venv/bin/python" "$ROOT/voice/scripts/prewarm_offline.py"
