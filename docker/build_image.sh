#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
export HOST_UID="${HOST_UID:-$(id -u)}"
export HOST_GID="${HOST_GID:-$(id -g)}"
export UNITREE_SDK2_CONTEXT="${UNITREE_SDK2_CONTEXT:-/mnt/ssd/navgation/projects/unitree_sdk2}"

for required_path in \
  "${UNITREE_SDK2_CONTEXT}/include" \
  "${UNITREE_SDK2_CONTEXT}/lib/aarch64/libunitree_sdk2.a" \
  "${UNITREE_SDK2_CONTEXT}/thirdparty/lib/aarch64/libddsc.so" \
  "${UNITREE_SDK2_CONTEXT}/thirdparty/lib/aarch64/libddscxx.so"; do
  if [[ ! -e "${required_path}" ]]; then
    echo "Missing Unitree SDK build context component: ${required_path}" >&2
    exit 1
  fi
done

printf '[%s] [INFO] building image with embedded Unitree SDK context=%s\n' \
  "$(date '+%Y-%m-%dT%H:%M:%S%z')" "${UNITREE_SDK2_CONTEXT}"
docker compose -f docker-compose.yml build
