#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/mnt/ssd/navgation/projects/temp/unitree_slam_example_new}"
EXAMPLE_DIR="${EXAMPLE_DIR:-${PROJECT_ROOT}/example}"
BUILD_DIR="${BUILD_DIR:-${EXAMPLE_DIR}/build_docker}"
PROJECTS_ROOT="${PROJECTS_ROOT:-/mnt/ssd/navgation/projects}"
CUSTOM_ACTION_WS="${CUSTOM_ACTION_WS:-${PROJECT_ROOT}/custom_action_ws}"
CUSTOM_ACTION_INSTALL="${CUSTOM_ACTION_INSTALL:-}"
UNITREE_SDK2_ROOT="${UNITREE_SDK2_ROOT:-${PROJECTS_ROOT}/unitree_sdk2}"
EMBEDDED_APP_ROOT="${EMBEDDED_APP_ROOT:-/opt/unitree_slam}"
EMBEDDED_CUSTOM_ACTION_INSTALL="${EMBEDDED_CUSTOM_ACTION_INSTALL:-${EMBEDDED_APP_ROOT}/custom_action_ws}"
EMBEDDED_NODE_BIN_DIR="${EMBEDDED_NODE_BIN_DIR:-${EMBEDDED_APP_ROOT}/nodes/bin}"

log_info() {
  printf '[%s] [INFO] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

validate_unitree_sdk() {
  local arch
  arch="$(uname -m)"
  if [ "${arch}" = "arm64" ]; then
    arch="aarch64"
  fi

  local required_paths=(
    "${UNITREE_SDK2_ROOT}/include"
    "${UNITREE_SDK2_ROOT}/lib/${arch}/libunitree_sdk2.a"
    "${UNITREE_SDK2_ROOT}/thirdparty/include"
    "${UNITREE_SDK2_ROOT}/thirdparty/lib/${arch}/libddsc.so"
    "${UNITREE_SDK2_ROOT}/thirdparty/lib/${arch}/libddscxx.so"
  )
  local required_path
  for required_path in "${required_paths[@]}"; do
    if [ ! -e "${required_path}" ]; then
      echo "Embedded Unitree SDK component is missing: ${required_path}" >&2
      exit 1
    fi
  done

  log_info "validated embedded Unitree SDK root=${UNITREE_SDK2_ROOT} arch=${arch}"
}

resolve_custom_action_install() {
  if [ -n "${CUSTOM_ACTION_INSTALL}" ]; then
    return
  fi
  if [ -f "${CUSTOM_ACTION_WS}/install/setup.bash" ]; then
    CUSTOM_ACTION_INSTALL="${CUSTOM_ACTION_WS}/install"
  else
    CUSTOM_ACTION_INSTALL="${EMBEDDED_CUSTOM_ACTION_INSTALL}"
  fi
}

validate_embedded_application() {
  local required_paths=(
    "${EMBEDDED_CUSTOM_ACTION_INSTALL}/setup.bash"
    "${EMBEDDED_NODE_BIN_DIR}/keyDemo_bin"
    "${EMBEDDED_NODE_BIN_DIR}/goGoalNavigation_bin"
    "${EMBEDDED_NODE_BIN_DIR}/goGoalNavigation66_bin"
    "${EMBEDDED_NODE_BIN_DIR}/gestureTopicBridge_bin"
    "${EMBEDDED_NODE_BIN_DIR}/g1Arm7DIYActionExample_bin"
    "${EMBEDDED_NODE_BIN_DIR}/g1ArmOfficialActionServer_bin"
  )
  local required_path
  for required_path in "${required_paths[@]}"; do
    if [ ! -e "${required_path}" ]; then
      echo "Embedded application component is missing: ${required_path}" >&2
      exit 1
    fi
  done
  log_info "validated embedded custom actions and six nodes root=${EMBEDDED_APP_ROOT}"
}

source_ros() {
  set +u
  source /opt/ros/humble/setup.bash
  set -u
}

source_env() {
  export RMW_IMPLEMENTATION="${HUMBLE_RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
  export ROS_LOG_DIR="${HUMBLE_ROS_LOG_DIR:-${ROS_LOG_DIR:-/tmp/unitree-slam-ros/log}}"
  mkdir -p "${ROS_LOG_DIR}"
  validate_unitree_sdk
  validate_embedded_application
  source_ros
  resolve_custom_action_install
  if [ ! -f "${CUSTOM_ACTION_INSTALL}/setup.bash" ]; then
    echo "custom_action_ws setup is unavailable: ${CUSTOM_ACTION_INSTALL}/setup.bash" >&2
    exit 1
  fi

  set +u
  source "${CUSTOM_ACTION_INSTALL}/setup.bash"
  set -u

  local arch
  arch="$(uname -m)"
  if [ "${arch}" = "arm64" ]; then
    arch="aarch64"
  fi

  export LD_LIBRARY_PATH="${UNITREE_SDK2_ROOT}/thirdparty/lib/${arch}:/opt/ros/humble/lib:${CUSTOM_ACTION_INSTALL}/lib:${CUSTOM_ACTION_INSTALL}/custom_action_interfaces/lib:${LD_LIBRARY_PATH:-}"
  export CMAKE_PREFIX_PATH="${CUSTOM_ACTION_INSTALL}:${CMAKE_PREFIX_PATH:-}"
  log_info "selected custom action install=${CUSTOM_ACTION_INSTALL} rmw=${RMW_IMPLEMENTATION} ros_domain_id=${ROS_DOMAIN_ID:-0} ros_log_dir=${ROS_LOG_DIR}"
}

build_custom_action_ws() {
  if [ ! -d "${CUSTOM_ACTION_WS}/src" ]; then
    echo "Missing custom_action_ws source directory: ${CUSTOM_ACTION_WS}/src" >&2
    exit 1
  fi

  source_ros
  cd "${CUSTOM_ACTION_WS}"
  colcon build --merge-install --symlink-install \
    --cmake-args -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-Release}"
}

check_embedded_runtime() {
  source_env
  local target_path
  local missing
  for target_path in "${EMBEDDED_NODE_BIN_DIR}"/*_bin; do
    missing="$(ldd "${target_path}" | grep 'not found' || true)"
    if [ -n "${missing}" ]; then
      echo "Missing runtime dependency for ${target_path}: ${missing}" >&2
      exit 1
    fi
  done
  log_info "embedded runtime check passed nodes=6 bin_dir=${EMBEDDED_NODE_BIN_DIR}"
}

usage() {
  cat <<USAGE
Usage:
  unitree-slam-example build [extra cmake --build args]
  unitree-slam-example build-actions
  unitree-slam-example check
  unitree-slam-example run <target> [target args]
  unitree-slam-example shell
  unitree-slam-example clean

Targets:
  keyDemo
  goGoalNavigation
  goGoalNavigation66
  gestureTopicBridge
  g1Arm7DIYActionExample
  g1ArmOfficialActionServer
USAGE
}

cmd="${1:-shell}"
if [ $# -gt 0 ]; then
  shift
fi

case "${cmd}" in
  build)
    log_info "starting Humble project build source=${EXAMPLE_DIR} build=${BUILD_DIR}"
    build_custom_action_ws
    CUSTOM_ACTION_INSTALL="${CUSTOM_ACTION_WS}/install"
    source_env
    rm -f "${BUILD_DIR}/CMakeCache.txt"
    cmake -S "${EXAMPLE_DIR}" -B "${BUILD_DIR}" \
      -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-Release}" \
      -DPROJECTS_ROOT="${PROJECTS_ROOT}" \
      -DUNITREE_SDK2_ROOT="${UNITREE_SDK2_ROOT}" \
      -DCUSTOM_ACTION_WS="${CUSTOM_ACTION_WS}" \
      -DCUSTOM_ACTION_INSTALL="${CUSTOM_ACTION_INSTALL}"
    cmake --build "${BUILD_DIR}" -j "${BUILD_JOBS:-$(nproc)}" "$@"
    ;;
  build-actions)
    build_custom_action_ws
    ;;
  check)
    check_embedded_runtime
    ;;
  run)
    if [ $# -lt 1 ]; then
      usage >&2
      exit 2
    fi
    target="$1"
    shift
    source_env
    target_path="${BUILD_DIR}/${target}"
    target_origin="mounted-source"
    if [ ! -x "${target_path}" ]; then
      target_path="${EMBEDDED_NODE_BIN_DIR}/${target}_bin"
      target_origin="embedded-image"
    fi
    if [ ! -x "${target_path}" ]; then
      echo "Unknown or unavailable target: ${target}" >&2
      usage >&2
      exit 2
    fi
    log_info "starting target=${target} origin=${target_origin} args_count=$# interface_hint=${1:-not-provided}"
    exec "${target_path}" "$@"
    ;;
  shell)
    source_ros
    resolve_custom_action_install
    if [ -f "${CUSTOM_ACTION_INSTALL}/setup.bash" ]; then
      set +u
      source "${CUSTOM_ACTION_INSTALL}/setup.bash"
      set -u
    fi
    if [ -d "${PROJECT_ROOT}" ]; then
      cd "${PROJECT_ROOT}"
    else
      cd "${EMBEDDED_APP_ROOT}"
    fi
    exec bash -l
    ;;
  clean)
    rm -rf "${BUILD_DIR}" "${CUSTOM_ACTION_WS}/build" "${CUSTOM_ACTION_WS}/install" "${CUSTOM_ACTION_WS}/log"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    exec "${cmd}" "$@"
    ;;
esac
