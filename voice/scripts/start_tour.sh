#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"
PYTHON_BIN="${VENV_ROOT}/bin/python"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf '[%s] [ERROR] tour Python is unavailable path=%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$PYTHON_BIN" >&2
  exit 1
fi

set +u
if [[ -f "${PROJECT_ROOT}/runtime/voice.env" ]]; then
  set -a
  source "${PROJECT_ROOT}/runtime/voice.env"
  set +a
fi
set -u

export VOICE_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export NAV_BRIDGE_URL="${NAV_BRIDGE_URL:-http://127.0.0.1:8765}"
export ARM_BRIDGE_URL="${ARM_BRIDGE_URL:-http://127.0.0.1:8765/navi_arm}"
export HF_HOME="${HF_HOME:-${PROJECT_ROOT}/hf_cache}"
export HUGGINGFACE_HUB_CACHE="${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HOME/hub}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export TRANSFORMERS_OFFLINE="${TRANSFORMERS_OFFLINE:-1}"
export HF_DATASETS_OFFLINE="${HF_DATASETS_OFFLINE:-1}"
export ENABLE_SEARCH="${ENABLE_SEARCH:-1}"

if [[ -z "${DASHSCOPE_API_KEY:-${OPENAI_API_KEY:-}}" ]]; then
  echo "Missing DASHSCOPE_API_KEY or OPENAI_API_KEY in ${PROJECT_ROOT}/runtime/voice.env" >&2
  exit 1
fi

log_info "starting venv tour root=${PROJECT_ROOT} python=${PYTHON_BIN} nav_bridge=${NAV_BRIDGE_URL} arm_bridge=${ARM_BRIDGE_URL}"
exec "$PYTHON_BIN" -m voice_console.supervisor
