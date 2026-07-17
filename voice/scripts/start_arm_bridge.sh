#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="${VOICE_PROJECT_ROOT:-/home/unitree/RobotABrainOnline/voice}"
ROS_INTERFACE="${ROS_INTERFACE:-eth0}"
ROS_INTERFACE_IP="$(ip -4 -o addr show "$ROS_INTERFACE" | awk '{split($4, parts, "/"); print parts[1]; exit}')"

if [[ -z "$ROS_INTERFACE_IP" ]]; then
  echo "Unable to resolve IPv4 address for ROS interface: ${ROS_INTERFACE}" >&2
  exit 1
fi
mkdir -p "${PROJECT_ROOT}/runtime"
sed "s/__ROS_INTERFACE_IP__/${ROS_INTERFACE_IP}/g" \
  "${PROJECT_ROOT}/deploy/fastdds-eth0.xml" > "${PROJECT_ROOT}/runtime/fastdds.xml"

printf '[%s] [INFO] starting Humble ROS bridge url=http://127.0.0.1:8765 interface=%s address=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$ROS_INTERFACE" "$ROS_INTERFACE_IP"
exec docker compose -f "${PROJECT_ROOT}/docker-compose.bridge.yml" up --no-deps arm-bridge
