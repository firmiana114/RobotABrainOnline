#!/usr/bin/env bash
set -Eeo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
STACK_ROOT="${VOICE_STACK_ROOT:-/home/unitree/RobotABrainOnline}"
VENV_ROOT="${VOICE_VENV_ROOT:-${PROJECT_ROOT}/.venv}"
NAV_IFACE="${NAV_IFACE:-eth0}"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

set +u
source "${VENV_ROOT}/bin/activate"
set -u

export VOICE_PROJECT_ROOT="$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"
MAP_PATH="$(python3 -m voice_console.generate --print-map)"
log_info "starting Docker navigation target=goGoalNavigation66 iface=${NAV_IFACE} map=${MAP_PATH}"
exec "${STACK_ROOT}/docker/run_example.sh" goGoalNavigation66 "$NAV_IFACE" "$MAP_PATH"
