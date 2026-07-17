#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"
source docker/configure_fastdds.sh
configure_fastdds "${ROS_INTERFACE:-}"
IMAGE_NAME="${IMAGE_NAME:-unitree_slam_example_new:humble}"
if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "Required image is not available: ${IMAGE_NAME}" >&2
  echo "Build or load the image before compiling the source project." >&2
  exit 1
fi
printf '[%s] [INFO] compiling source with existing self-contained image=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${IMAGE_NAME}"
docker compose -f docker-compose.yml run --rm slam-example build "$@"
