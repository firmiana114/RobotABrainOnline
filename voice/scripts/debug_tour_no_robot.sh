#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -f "$ROOT/voice/runtime/voice.env" ]]; then
  set -a
  source "$ROOT/voice/runtime/voice.env"
  set +a
fi
export PYTHONPATH="$ROOT/voice:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="${VOICE_RUNTIME_LIB:-$ROOT-runtime/lib}:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"
export HF_HOME="${HF_HOME:-$ROOT/voice/hf_cache}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$ROOT/voice/.venv/bin/python" -m voice_console.debug_no_robot "$@"
