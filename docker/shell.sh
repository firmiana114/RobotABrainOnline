#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"
source docker/configure_fastdds.sh
configure_fastdds "${ROS_INTERFACE:-}"
docker compose -f docker-compose.yml run --rm slam-example shell
