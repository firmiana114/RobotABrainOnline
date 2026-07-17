#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
STACK_ROOT="${VOICE_STACK_ROOT:-/home/unitree/RobotABrainOnline}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

source "${VENV_ROOT}/bin/activate"

export VOICE_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
export POSE_BRIDGE_URL="${POSE_BRIDGE_URL:-http://127.0.0.1:8765/pose}"
log_info "starting voice console root=${PROJECT_ROOT} port=${VOICE_CONSOLE_PORT:-8081} pose_bridge=${POSE_BRIDGE_URL}"
exec python3 -m voice_console
