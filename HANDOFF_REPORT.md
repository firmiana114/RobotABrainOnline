# HANDOFF_REPORT

## 项目整体描述

- 统一仓库路径：本机 `/Users/firmiana/Desktop/RobotABrainOnline`，Orin NX `/home/unitree/RobotABrainOnline`；AGX `/mnt/ssd/navgation/projects/temp/unitree_slam_example_new` 仅为历史部署副本。
- 用途：为 Unitree G1 提供 SLAM、导航、手臂动作、手势桥、语音导览、Qwen 问答、Kokoro 中文 TTS 和 8081 前端控制台。
- 技术栈：Ubuntu 22.04/aarch64、ROS 2 Humble、C++17、CMake/colcon、Unitree SDK2、Fast DDS、Docker Compose；NX 宿主使用 Python 3.8 venv、FastAPI、DashScope、Kokoro/PyTorch CUDA 和 ALSA。
- 架构硬约束：宿主 venv 不 source Foxy、不 import `rclpy`、不安装 `custom_action_interfaces`；所有 ROS 2、自定义 Action、导航和手臂节点均在 Humble 容器中运行。

## 核心模块与目录

- `example/src/`：六个 C++ 节点源码。`keyDemo` 负责建图/重定位；`goGoalNavigation*` 负责导航；`g1Arm*` 负责手臂动作；`gestureTopicBridge` 负责手势桥接。
- `custom_action_ws/src/`：ROS 2 自定义 Action 源码。
- `docker/`、`docker-compose.yml`：Humble 镜像、编译、运行、自检和 Fast DDS 配置入口。
- `voice/voice_console/`：8081 网页、REST API、配置管理、服务状态和 HTTP 位姿轮询。
- `voice/nav_route_then_speak.py`：宿主导览编排、STT、Qwen 和 Kokoro，使用 HTTP 调用 Humble 桥。
- `voice/arm_action_bridge.py`：仅在 Humble 容器运行，桥接 `/go_goal_pose`、`/nav_status`、`/current_pose` 和 `/navi_arm`。
- `voice/docker-compose.bridge.yml`：Humble HTTP 桥，使用 host network/IPC，仅监听 `127.0.0.1:8765`。
- `voice/conf/dialogue_0.json`：标准导览配置；`dialogue_0_route.json` 和 `guide_qa_context.txt` 是派生内容。
- `voice/guide_cache/`：预生成播报；`voice/hf_cache/`：Kokoro 离线模型缓存，不纳入 Git。

## 核心数据流

1. 浏览器通过 NX 的 8081 端口访问控制台。
2. 导览、DashScope/Qwen 请求、STT 和 Kokoro CUDA 合成在 NX 宿主 venv 运行，避免额外容器网络延迟。
3. 宿主导览通过 `127.0.0.1:8765` HTTP 把导航目标和手臂动作交给 Humble 桥。
4. Humble 桥通过 ROS topic/action 与导航、手臂节点通信；节点再经 `eth0` 上的 Unitree DDS/RPC 访问机器人 PC1。
5. 地图路径只是 RPC 字符串；`/home/unitree/test7.pcd` 由 PC1 本地 SLAM 服务解析，不需要存在于 NX 或挂载进 Docker。

## Docker 与 ROS 环境

- 镜像标签：`unitree_slam_example_new:humble`。
- 镜像内 Unitree SDK2：`/opt/unitree_sdk2`，约 411 MB；自定义 Action：`/opt/unitree_slam/custom_action_ws`；六个节点：`/opt/unitree_slam/nodes/bin`。
- 镜像使用 ROS 2 Humble、Python 3.10 和 `rmw_fastrtps_cpp`；`HUMBLE_RMW_IMPLEMENTATION` 会隔离宿主 Foxy 环境。
- 镜像包含 UID/GID 1000 的 `unitree` 用户，满足 SDK `getpwuid` 要求并保持挂载产物归宿主用户所有。
- `docker/build_image.sh` 重建镜像；`docker/build_example.sh` 使用既有镜像编译源码；`docker/run_example.sh <target> <iface> ...` 运行节点；`docker/shell.sh` 进入环境。
- `run_example.sh`、`build_example.sh` 和 `shell.sh` 会自动生成 `runtime/fastdds.xml`，禁用跨容器 SHM 并将 DDS UDP 固定到机器人网卡。
- 镜像已内置 SDK、自定义 Action 和六个节点，因此“源码 + 既有镜像”可以工作；宿主无需再挂载 SDK。

## NX 环境、网络与运行时

- NX：`wlan0=172.16.2.156/24` 负责公网，`eth0=192.168.123.164/24` 负责机器人网；PC1 为 `192.168.123.161`。
- Wi-Fi 默认网关为 `172.16.2.1`；公网 IP、DNS、HTTPS、Ubuntu 软件源和 PC1 ICMP 已验证。GitHub、Docker Registry 或 Hugging Face 仍可能超时，必要时可经本机 1082 端口建立 SSH 代理转发。
- venv：`/home/unitree/RobotABrainOnline/voice/.venv`；Kokoro 缓存：`voice/hf_cache`；私密配置：`voice/runtime/voice.env`，权限 0600。
- 运行时密钥、venv、模型缓存、日志、构建产物和离线 wheel 均被 `.gitignore` 排除，不得输出 `voice.env` 内容。
- NVIDIA JetPack 5.1.1 PyTorch `2.0.0+nv23.05` 已安装，`torch.cuda.is_available()` 为 true；Kokoro 0.9.4 已使用离线缓存完成 CUDA 合成。
- Qwen 已通过宿主 venv 的 DashScope 实际调用成功，探测时未记录 Key 或响应正文。
- 音频设备已枚举：无线麦克风 `plughw:CARD=Rx,DEV=0`，REDMI 扬声器 `plughw:CARD=R26002,DEV=0`；真实 STT 和外放仍待端到端验证。

## systemd 服务与重要警告

- 已安装 `voice-control-console`、`voice-arm-bridge`、`voice-navigation`、`voice-arm-control`、`voice-tour-guide` 五个 unit。
- 截至本报告更新时，五个服务全部为 `disabled/inactive`，没有相关桥容器残留。
- **未经用户明确授权，不得启动、重启或 enable 任何上述服务，也不得执行真实导航、重定位或手臂动作。**
- **不要直接运行 `voice/scripts/install_services.sh`：该脚本末尾包含 `systemctl enable --now voice-control-console.service voice-arm-bridge.service`，会启动服务。**
- 只更新 unit 时应手动复制 `voice/deploy/systemd/*.service` 和 sudoers 文件，校验后仅执行 `systemctl daemon-reload`，再确认五个服务仍为 `disabled/inactive`。

安全只读命令：

```bash
ssh NX
cd /home/unitree/RobotABrainOnline
git status --short
systemctl is-enabled voice-control-console voice-arm-bridge voice-navigation voice-arm-control voice-tour-guide
systemctl is-active voice-control-console voice-arm-bridge voice-navigation voice-arm-control voice-tour-guide
docker run --rm unitree_slam_example_new:humble check
```

## `post_wait_seconds` 修复

- 原因：`nav_route_then_speak.load_speech_segments()` 重建台词段时只复制文本，丢弃了 `post_wait_seconds`，执行阶段因此无法看到点位 8 的 277 秒等待。
- 修复：路线加载时保留该字段，由 `voice_console/post_wait_runner.py` 统一归一化并在台词播报完成后等待。
- 诊断日志会输出 `[route] post-wait start seconds=...` 和 `[route] post-wait complete seconds=...`，便于确认等待生效，不记录台词或隐私内容。
- 回归测试覆盖字段保留、非法值、零等待和短等待执行链；点位 8 的 277 秒配置以及 0.05 秒短等待链路已验证。

## 导览语音打断并发竞态修复

- 根因：旧实现由播报打断主线程和后台空闲问答线程共同消费 `question_queue`；后台还会在非 `idle` 状态反复取出再放回。WAV 自然结束边界没有保留唤醒归属，最终识别稍晚到达时会被后台线程接走，主导览则已推进下一段。
- `voice_console/interrupt.py` 新增 `RouteInterruptCoordinator`，为每次台词播放分配 attempt、为每次打断分配 session。播报中的中间唤醒先声明 session 再停止播放器，最终问题只能提交给该 session。
- WAV 正常退出后默认保留 0.9 秒有界边界窗口；窗口内到达的唤醒仍归属当前台词。当前打断完成前主线程不会返回，Qwen 推理和回答播放期间无法推进下一段。
- 打断问题不再进入后台队列；空闲问题使用独立有界队列和一个常驻串行消费者，不再反复回队或为每个问题创建线程。迟到的同一最终结果会在短窗口内去重，避免重复回答。
- “继续导览”、无完整问题超时和回答完成后仍从当前台词开头重播；回答异常先播放降级提示，处理结束后才恢复。
- `play_audio_file()` 检查 `aplay` 返回码并区分正常完成、主动打断和播放失败。非零退出或启动/终止异常会抛出保留异常链的 `AudioPlaybackError`，不再被当作台词成功完成。
- 新增 INFO/ERROR 诊断日志：attempt/session 生命周期、边界归属、问题片段数量与长度、回答阶段、音频设备、文件名和返回码；不记录识别全文、问题全文、密钥或音频数据。
- 本机全量测试为 `32 passed, 2 skipped`，跳过项依赖本机未安装的 DashScope/完整语音栈；新增纯并发测试连续运行 10 轮均通过。NX Python 3.8 环境候选副本全量测试为 `34 passed`。
- 未启动导航、手臂或完整导览，未发送机器人动作。真实麦克风、扬声器、慢 Qwen 网络和 WAV 自然结束边界仍需现场安全条件下验证。

## Kokoro 成熟男性音色

- Kokoro v1.1-zh 官方仓库包含 55 个 `zf_*` 中文女声和 45 个 `zm_*` 中文男声，但没有年龄或风格标签。
- 使用同一句中文导览词在 NX CUDA 上抽样比较 12 个男声；`zm_029` 的基频中位数约 104 Hz、波动约 16.5 Hz，是样本中最低且最稳定的候选，因此作为成熟男性默认音色。
- `voice_console.config.DEFAULT_KOKORO_VOICE` 统一设为 `zm_029`；导览 supervisor 显式传入 `--kokoro-voice`，并允许通过 `KOKORO_VOICE` 环境变量覆盖。
- 导览 WAV 缓存键现在包含模型、voice、语速和文本。旧 `zf_001` 缓存仍可保留但不会被新音色误用，首次正式播报会生成新的 `zm_029` 缓存。
- NX 离线模型缓存已补齐 `zm_029.pt`；`HF_HUB_OFFLINE=1` 下完成 CUDA 合成，3.1 秒验证音频生成成功，未播放音频、未启动导览或机器人节点。
- 回归测试覆盖默认音色、环境变量覆盖和跨音色缓存隔离；本机全量为 `34 passed, 3 skipped`，NX Python 3.8 候选副本为 `37 passed`。

## 已验证事实

- `docker run --rm unitree_slam_example_new:humble check` 成功；六个节点 ELF 的 `ldd` 无缺失依赖，RUNPATH 指向镜像内 SDK 和自定义 Action。
- 无宿主 SDK 上下文仍可使用既有镜像编译 `custom_action_ws` 和六个节点。
- Compose、Shell 语法、Python compileall 和 systemd unit 静态检查通过。
- NX 自动化测试：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests` 为 `23 passed`。
- Humble 临时订阅者已收到 HTTP 桥发布的 `/go_goal_pose`；临时 `/current_pose` 已到达桥和控制台，验证跨容器 ROS 数据链但未驱动机器人。
- `goGoalNavigation eth0` 曾只完成 ROS/Unitree 初始化并保持 8 秒，未发送目标或启动重定位；真实运动链尚未验证。
- 项目已从 NX `/home/unitree/gt` 原地迁移到 `/home/unitree/RobotABrainOnline`，旧路径已移除。

## Git 与同步状态

- 根目录是 `main` 分支 Git 仓库，`voice/` 由根仓库统一管理，不要在子目录再次执行 `git init`。
- 2026-07-17 合并交接报告前，本机与 NX 已验证处于相同基线提交 `9eb2a5a342545e3de3d7b83cb4b3659f7977f491`，两端工作区均干净。
- 本报告本身会产生后续提交，因此当前部署提交应以两端 `git rev-parse HEAD` 的实际结果为准；每次修改后必须再次确认本机与 NX HEAD 相同且工作区干净。
- 修改代码或文档后必须更新本报告、创建中文 Git 提交，并把 NX 同步到同一提交。

## 剩余阻塞与下一步

1. 在不启动机器人运动节点的前提下，验证无线麦克风采音、STT、Kokoro 和 REDMI 外放链路。
2. 只有在现场安全确认并获得用户明确授权后，才能依次验证导航、重定位、手臂 Action 和完整导览。
3. 若需从 NX 访问 GitHub、镜像仓库或 Hugging Face，配置经本机 1082 的受控代理转发。
4. 不得在日志中记录 API Key、密码、完整识别文本或大体积 DDS 原始数据。
