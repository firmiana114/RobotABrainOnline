ARG BASE_IMAGE=ros:humble-ros-base-jammy

# Build the ROS environment here instead of relying on a private portable image.
FROM ${BASE_IMAGE} AS runtime-base
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libeigen3-dev \
    python3-colcon-common-extensions \
    ros-humble-ament-cmake \
    ros-humble-ament-lint-auto \
    ros-humble-ament-lint-common \
    ros-humble-rclpy \
    ros-humble-rclcpp \
    ros-humble-rclcpp-action \
    ros-humble-std-msgs \
    ros-humble-geometry-msgs \
    ros-humble-rosidl-default-generators \
    ros-humble-rmw-fastrtps-cpp \
    && rm -rf /var/lib/apt/lists/*

FROM runtime-base AS builder

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    ROS_DISTRO=humble \
    UNITREE_SDK2_ROOT=/opt/unitree_sdk2

USER root
COPY --from=unitree_sdk2 / /opt/unitree_sdk2/
COPY custom_action_ws/src /tmp/unitree_slam_src/custom_action_ws/src/
COPY example /tmp/unitree_slam_src/example/
RUN test -d /opt/unitree_sdk2/include \
    && test -f /opt/unitree_sdk2/lib/aarch64/libunitree_sdk2.a \
    && test -f /opt/unitree_sdk2/thirdparty/lib/aarch64/libddsc.so \
    && test -f /opt/unitree_sdk2/thirdparty/lib/aarch64/libddscxx.so \
    && bash -lc 'set -eo pipefail; \
        source /opt/ros/humble/setup.bash; \
        colcon --log-base /tmp/custom_action_log build \
          --merge-install \
          --base-paths /tmp/unitree_slam_src/custom_action_ws/src \
          --build-base /tmp/custom_action_build \
          --install-base /opt/unitree_slam/custom_action_ws \
          --cmake-args -DCMAKE_BUILD_TYPE=Release; \
        source /opt/unitree_slam/custom_action_ws/setup.bash; \
        cmake -S /tmp/unitree_slam_src/example -B /tmp/unitree_slam_build \
          -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/opt/unitree_slam/nodes \
          -DUNITREE_SDK2_ROOT=/opt/unitree_sdk2 \
          -DCUSTOM_ACTION_WS=/tmp/unitree_slam_src/custom_action_ws \
          -DCUSTOM_ACTION_INSTALL=/opt/unitree_slam/custom_action_ws; \
        cmake --build /tmp/unitree_slam_build -j "$(nproc)"; \
        cmake --install /tmp/unitree_slam_build'

FROM runtime-base

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    ROS_DISTRO=humble \
    UNITREE_SDK2_ROOT=/opt/unitree_sdk2 \
    EMBEDDED_APP_ROOT=/opt/unitree_slam \
    EMBEDDED_CUSTOM_ACTION_INSTALL=/opt/unitree_slam/custom_action_ws \
    EMBEDDED_NODE_BIN_DIR=/opt/unitree_slam/nodes/bin \
    RMW_IMPLEMENTATION=rmw_fastrtps_cpp \
    ROS_LOG_DIR=/tmp/unitree-slam-ros/log \
    HOME=/home/unitree \
    PROJECT_ROOT=/mnt/ssd/navgation/projects/temp/unitree_slam_example_new \
    EXAMPLE_DIR=/mnt/ssd/navgation/projects/temp/unitree_slam_example_new/example \
    BUILD_DIR=/mnt/ssd/navgation/projects/temp/unitree_slam_example_new/example/build_docker \
    PROJECTS_ROOT=/mnt/ssd/navgation/projects

USER root
COPY --from=builder /opt/unitree_sdk2/ /opt/unitree_sdk2/
COPY --from=builder /opt/unitree_slam/ /opt/unitree_slam/
COPY docker/entrypoint.sh /usr/local/bin/unitree-slam-example
RUN groupadd --gid 1000 unitree \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash unitree \
    && test -d /opt/unitree_sdk2/include \
    && test -f /opt/unitree_sdk2/lib/aarch64/libunitree_sdk2.a \
    && test -f /opt/unitree_sdk2/thirdparty/lib/aarch64/libddsc.so \
    && test -f /opt/unitree_sdk2/thirdparty/lib/aarch64/libddscxx.so \
    && test -f /opt/unitree_slam/custom_action_ws/setup.bash \
    && test -x /opt/unitree_slam/nodes/bin/keyDemo_bin \
    && test -x /opt/unitree_slam/nodes/bin/goGoalNavigation_bin \
    && test -x /opt/unitree_slam/nodes/bin/goGoalNavigation66_bin \
    && test -x /opt/unitree_slam/nodes/bin/gestureTopicBridge_bin \
    && test -x /opt/unitree_slam/nodes/bin/g1Arm7DIYActionExample_bin \
    && test -x /opt/unitree_slam/nodes/bin/g1ArmOfficialActionServer_bin \
    && chmod +x /usr/local/bin/unitree-slam-example

LABEL org.opencontainers.image.title="Unitree SLAM Humble nodes" \
      org.opencontainers.image.description="ROS 2 Humble runtime with embedded Unitree SDK2, custom actions and six nodes" \
      io.unitree.sdk.embedded="true" \
      io.unitree.sdk.path="/opt/unitree_sdk2" \
      io.unitree.custom-actions.embedded="true" \
      io.unitree.nodes.embedded="true"

WORKDIR /opt/unitree_slam
ENTRYPOINT ["/usr/local/bin/unitree-slam-example"]
CMD ["shell"]
