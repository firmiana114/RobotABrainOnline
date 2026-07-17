#!/usr/bin/env bash
set -Eeo pipefail

STACK_ROOT="${VOICE_STACK_ROOT:-/home/unitree/RobotABrainOnline}"
NAV_IFACE="${NAV_IFACE:-eth0}"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

log_info "starting Docker arm target=g1ArmOfficialActionServer iface=${NAV_IFACE}"
exec "${STACK_ROOT}/docker/run_example.sh" g1ArmOfficialActionServer "$NAV_IFACE"
