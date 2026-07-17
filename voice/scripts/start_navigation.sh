#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
STACK_ROOT="${VOICE_STACK_ROOT:-/home/unitree/RobotABrainOnline}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"
NAV_IFACE="${NAV_IFACE:-eth0}"
PYTHON_BIN="${VENV_ROOT}/bin/python"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

if [[ ! -x "$PYTHON_BIN" ]]; then
  printf '[%s] [ERROR] navigation Python is unavailable path=%s\n' \
    "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$PYTHON_BIN" >&2
  exit 1
fi

export VOICE_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
MAP_PATH="$("$PYTHON_BIN" -m voice_console.generate --print-map)"
log_info "starting Docker navigation target=goGoalNavigation66 iface=${NAV_IFACE} python=${PYTHON_BIN} map=${MAP_PATH}"
exec "${STACK_ROOT}/docker/run_example.sh" goGoalNavigation66 "$NAV_IFACE" "$MAP_PATH"
