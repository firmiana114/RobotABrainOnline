#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
STACK_ROOT="${VOICE_STACK_ROOT:-/home/unitree/RobotABrainOnline}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"
PYTHON_BIN="${VENV_ROOT}/bin/python"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf '[%s] [ERROR] voice console Python is unavailable path=%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$PYTHON_BIN" >&2
  exit 1
fi

export VOICE_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export POSE_BRIDGE_URL="${POSE_BRIDGE_URL:-http://127.0.0.1:8765/pose}"
log_info "starting voice console root=${PROJECT_ROOT} python=${PYTHON_BIN} port=${VOICE_CONSOLE_PORT:-8081} pose_bridge=${POSE_BRIDGE_URL}"
exec "$PYTHON_BIN" -m voice_console
