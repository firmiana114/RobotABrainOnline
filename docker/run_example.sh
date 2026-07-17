#!/usr/bin/env bash
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "Usage: $0 <target> [target args]" >&2
  exit 2
fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"
source docker/configure_fastdds.sh
configure_fastdds "${ROS_INTERFACE:-${2:-}}"
docker compose -f docker-compose.yml run --rm slam-example run "$@"
