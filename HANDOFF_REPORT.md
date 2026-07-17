# HANDOFF_REPORT

## 项目整体描述

- 统一仓库路径：本机 `/Users/firmiana/Desktop/RobotABrainOnline`，Orin NX `/home/unitree/RobotABrainOnline`；`voice/` 是同一仓库的宿主导览子项目。AGX `/mnt/ssd/navgation/projects/temp/unitree_slam_example_new` 仅为历史部署副本。
- 用途：为 Unitree G1 提供 ROS 2 Humble 下的 SLAM、导航、手臂动作和手势 DDS 桥接示例，以及宿主 venv 中的语音导览和前端控制台。
- 核心功能：`keyDemo` 负责建图/重定位交互；`goGoalNavigation` 与 `goGoalNavigation66` 提供导航 topic/action；`g1Arm7DIYActionExample` 与 `g1ArmOfficialActionServer` 提供手臂动作；`gestureTopicBridge` 提供手势话题桥接。
- 主要技术栈：Ubuntu 22.04、ROS 2 Humble、C++17、CMake、colcon/ament、Unitree SDK2、Cyclone DDS、Docker Compose、ARM64。
- 关键目录：`example/src` 为节点源码，`custom_action_ws/src` 为自定义 action，`docker` 为 Humble 镜像与入口脚本，`voice` 为宿主导览、控制台和 Humble HTTP 桥；镜像内安装根目录为 `/opt/unitree_slam`。
- 运行入口：`docker/build_image.sh` 构建镜像；`docker/build_example.sh` 使用已有镜像编译源码；`docker/run_example.sh <target> <iface> ...` 运行目标；`docker/shell.sh` 进入环境。
- 核心数据流：Humble ROS topic/action 进入 C++ 节点，节点通过指定网卡上的 Unitree DDS/RPC 与机器人 PC1 通信。地图路径字符串由 PC1 的 SLAM 服务解析。
- 重要配置：`docker-compose.yml` 使用 host network、host IPC；默认 `ROS_DOMAIN_ID=0`、`RMW_IMPLEMENTATION=rmw_fastrtps_cpp`；`docker/fastdds-eth0.xml` 为 UDP 网卡白名单模板。
- 外部依赖：构建镜像时需要 Unitree SDK2 上下文；镜像生成后已内置 SDK、自定义 Action 和六个节点，可脱离源码运行。真实业务运行仍需要机器人网口及 PC1 上的 Unitree 服务。
- 部署方式：AGX 网卡为 `eno1`；Orin NX 网卡为 `eth0`。NX 已导入自包含镜像并完成源码迁移；语音导览和控制台位于 NX `/home/unitree/RobotABrainOnline/voice`，由宿主 venv 运行并通过 Humble HTTP 桥访问本项目 ROS 节点。

## 当前状态

- Dockerfile 使用多阶段构建：Unitree SDK2 位于 `/opt/unitree_sdk2`，`custom_action_ws` 安装到 `/opt/unitree_slam/custom_action_ws`，六个节点安装到 `/opt/unitree_slam/nodes/bin`。
- Compose 已移除宿主机 SDK 运行时挂载。
- `build_example.sh`、`run_example.sh`、`shell.sh` 直接使用 Compose 的既有镜像运行；镜像重建仅由 `build_image.sh` 触发，确保“源码 + 已有镜像”即可工作。
- 容器入口支持 `check` 安全自检；`run` 优先使用源码挂载中的开发产物，不存在时自动使用镜像内节点。
- 当前镜像标签：`unitree_slam_example_new:humble`；AGX manifest ID 为 `sha256:5f93c0c87e4c53f0e1771e0fd10c848d6adf5c7d2afbd63ac60f19f72354b9bd`。
- NX 导入的是该 manifest 的 ARM64 单平台镜像，配置 ID 为 `sha256:b97cf3fbcf47d30378cf845eccaa07d63640a3304b6297cc3a3f954f0f7668e7`。
- Compose 使用 `HUMBLE_RMW_IMPLEMENTATION` 隔离宿主 Foxy 环境，默认强制 Humble 的 `rmw_fastrtps_cpp`；ROS 日志目录固定为 `/tmp/unitree-slam-ros/log`。
- 镜像包含 UID/GID 1000 的 `unitree` 运行用户，满足 Unitree SDK 的 `getpwuid` 要求，同时保持挂载目录产物归宿主用户所有。
- `run_example.sh`、`build_example.sh`、`shell.sh` 会按传入网卡或 `192.168.123.0/24` 自动生成 `runtime/fastdds.xml`，禁用跨容器易失效的 SHM 数据路径并把 Fast DDS UDP 固定到机器人网卡。
- 项目已初始化为 `main` 分支 Git 仓库；运行目录、venv、模型缓存、构建产物、日志和离线 wheel 均不纳入版本管理。

## 已验证事实

- 既有镜像基础环境为 Ubuntu 22.04/aarch64、ROS 2 Humble、Python 3.10。
- 自包含镜像已重建成功；`/opt/unitree_sdk2` 占用约 411 MB，OCI 标签 `io.unitree.sdk.embedded=true`。
- 镜像内 SDK 三个关键库与构建来源的 SHA-256 一致：`libunitree_sdk2.a`、`libddsc.so`、`libddscxx.so`。
- 无源码挂载执行 `docker run --rm unitree_slam_example_new:humble check` 成功，六个镜像内 ELF 的 `ldd` 均无缺失依赖。
- 无源码挂载时可正常解析 `custom_action_interfaces/action/NaviArm`，六个节点 RUNPATH 均包含 `/opt/unitree_slam/custom_action_ws/lib`。
- 已在独立源码副本中把 `UNITREE_SDK2_CONTEXT` 指向不存在路径，仍使用现有镜像成功编译全部六个目标，证明源码编译不依赖宿主机 SDK。
- AGX 正式项目目录已使用包含安装产物的新镜像重新编译全部六个目标成功，开发模式未发生回归。
- 项目已从 NX `/home/unitree/gt` 原地迁移为 `/home/unitree/RobotABrainOnline`；NX 已安装 Docker Compose 2.27.1，并曾成功重新编译 `custom_action_ws` 和全部六个节点。
- NX 用户 `unitree` 已加入 `docker` 组，普通 SSH 会话可直接访问 Docker daemon。
- NX 网络已修复：Wi-Fi `wlan0` 默认网关为 `172.16.2.1`，机器人网 `192.168.123.0/24` 仍经 `eth0`；公网 IP、DNS、HTTPS、Ubuntu 软件源以及 PC1 `192.168.123.161` 的 ICMP 均验证可用。
- NX 时区为 `Asia/Shanghai`，NTP 同步状态已验证为正常。
- 已故意从宿主传入错误的 `rmw_cyclonedds_cpp`，Compose 仍选择 `rmw_fastrtps_cpp`；镜像自检通过。
- `goGoalNavigation eth0` 不带地图参数在 NX 成功完成 ROS 2/Unitree 初始化并保持运行 8 秒，未发送导航目标或启动重定位；SIGINT 退出时节点记录了 `Stop slam success`。
- 六个包装入口和六个 `_bin` 产物均存在；全部六个 ELF 的 `ldd` 无缺失依赖，RUNPATH 指向镜像内 `/opt/unitree_sdk2/thirdparty/lib/aarch64`。
- `docker compose config` 确认运行时仅挂载项目源码和 X11，不再挂载宿主机 Unitree SDK。
- PC1 为 `192.168.123.161`；地图 `/home/unitree/test7.pcd` 由 PC1 解析，不需要挂载到容器。
- 使用相同 Fast DDS 网卡配置的临时 Humble 订阅者已收到 Humble HTTP 桥发布的 `/go_goal_pose`；临时 `/current_pose` 也已到达桥和宿主控制台，验证了跨容器 ROS 数据链但未驱动机器人。
- 动态 Fast DDS 配置已分别在 NX 生成 `192.168.123.164`、在 AGX 生成 `192.168.123.222`，两端 Compose 校验通过。

## 阻塞问题

- NX 到 GitHub 和 Docker Registry 的 HTTPS 连接仍可能超时；普通公网、DNS 和 Ubuntu 软件源已恢复，必要时可通过本机 1082 端口建立 SSH 代理转发。
- NX 上尚未执行真实导航、重定位或手臂动作，机器人运动端到端通信仍待现场安全验证。
- 导览项目已迁移并验证控制台、Humble 桥、Qwen 和 Kokoro；NX 已枚举无线麦克风 `plughw:CARD=Rx,DEV=0` 和 REDMI 扬声器 `plughw:CARD=R26002,DEV=0`，但真实采音、识别和外放链路尚未端到端验证。

## 下一步

1. 在不启动运动节点的前提下，确认 `ALSA_INPUT_DEVICE`、`SPEAKER_NAME` 并验证采音和播放。
2. 经现场安全确认后依次启动 `voice-navigation`、`voice-arm-control`，再启动 `voice-tour-guide` 验证真实运动链。
3. 若需要 NX 直接访问 GitHub 或镜像仓库，配置经本机 1082 的受控代理转发。

## 注意事项

- 未经现场安全确认不得执行导航、重定位或手臂动作。
- 不要在日志中记录 API Key、密码、完整语音输入或大体积 DDS 原始数据。
- 若修改 SDK 版本，必须重新构建镜像并重新验证 DDS 动态库优先级。
- 宿主 venv 不得 source Foxy 或直接创建 ROS 节点；所有 ROS 和 `custom_action_interfaces` 必须留在 Humble 容器。
- 当前五个 `voice-*` systemd 服务按要求均保持 `disabled/inactive`；只有现场明确授权后才能启动。
