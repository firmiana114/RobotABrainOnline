# HANDOFF_REPORT

## new-orin 离线部署（2026-09-09 检查）

系统音频播放更新：默认 `aplay -D pulse`，正式导览 supervisor、路线助手与离线调试统一使用系统输出，不再通过 SPEAKER_NAME 自动直连 plughw。保留 `APLAY_DEVICE`/`--aplay-device` 显式覆盖。调试预检查拒绝虚拟空输出，音频服务不可用时不回退到硬件。77 项测试通过，设备预检查通过；真实短语音的 PulseAudio 流已观察到，路由到默认 REDMI USB 音响，播放前后系统音量保持 12%。这证明路由和音量设置未被覆盖，不代表现场听感已经确认。 新增硬件语音调试入口 `voice/scripts/debug_tour_no_robot.sh`，用法见 `voice/DEBUG_NO_ROBOT.md`。默认 DJI MIC MINI、首个路线项，导航/手臂由进程内模拟替代；不创建真实 ROS/HTTP 客户端，不改变正式服务。支持选点、模拟行走时间和台词等待上限。70 项测试通过；实机真实麦克风 + 实时 STT + CUDA + ALSA null 输出的短路线完成并清理采集进程。音响尚未作为实际输出接通，真实外放/打断交互未验证；预检查会拒绝找不到的默认音响。

此节描述 `new-orin`，与下文历史 NX 部署分开。路径为 `/mnt/disk1/gt/RobotABrainOnline`，账号为 `pc`。

- 使用用户级 systemd，所有服务操作必须加 `--user`。五个 unit 已安装；控制台与 HTTP/ROS 桥 active、enabled；导航、手臂、导览 inactive、disabled。用户 Linger=yes。
- `python3 voice/scripts/install_user_services.py` 可重新生成适配当前目录的 unit，不启动服务，不覆盖已有运行时配置。控制台通过 `voice/runtime/systemctl-user` 管理用户级服务。
- 系统 CUDA 的 `libcusparseLt.so.0` 错误链接到了 `libcusparse.so`。本次未改系统库，服务通过 `LD_LIBRARY_PATH` 优先使用旁目录 `../RobotABrainOnline-runtime/lib` 的正确库。Torch CUDA 运算通过；`pip check` 无冲突。
- `voice/scripts/prewarm_offline.sh` 使用正式分块合成和缓存逻辑，无需 Key，不创建云客户端、不录音、不播放、不调用机器人。真实 CUDA 合成 34 段，均可读取、采样率 24000 Hz、数据有限，总时长 496 秒；另执行一次强制新合成探针。运行日志与派生路线位于 `voice/runtime/`，不进入 Git。
- 镜像 `unitree_slam_example_new:humble` 的离线 check 通过，SDK、自定义 Action、六个节点及动态库完整。桥 `/healthz` 返回 ok，`/nav_state` 的订阅者为 0，与机器人离线一致；控制台首页 HTTP 200。
- 原有测试 66 passed；新增离线模式测试 1 passed，验证无 Key 时离线模式不创建云客户端，正式模式仍拒绝无 Key。
- `voice/runtime/deployment.env` 当前 `ROS_INTERFACE=lo`，桥只使用回环接口。现场机器人候选有线口 `eno1` 当前无链路；未修改 IP、路由，未启动运动节点。
- `voice/runtime/voice.env` 已由用户填入 Key（权限仍为 0600）。已实际验证 Qwen 问答（1.23 秒）及 paraformer-realtime-v2 识别（0.67 秒）：合成测试音频中的四个关键词均匹配。未输出 Key、识别全文或回答正文。
- 完整无硬件验证通过：CUDA 合成测试提问 → 云端 STT → 项目 `stream_qwen_and_speak_by_sentence`（开启搜索）→ CUDA 回答合成，生成可读取、24000 Hz、数据有限的 2.5 秒 WAV，全程 16.16 秒（含模型初始化）。仅在独立验证进程中将播放函数替换为音频文件校验；没有录音、外放或机器人调用。结果位于 `voice/runtime/cloud_verification.json` 和 `voice/runtime/pipeline_verification.json`；探针脚本及测试音频也位于 runtime，不进入 Git。
- 未检测到文档指定的 Rx 麦克风及 REDMI 扬声器，实际采集和外放待设备连接。导览常驻服务仍保持 inactive，未绕过真实导航订阅者就绪检查。
- 公网修复：同网段本机 DHCP 确认路由器/DNS 为 192.168.101.254；为 Tsinghua-WX-5G 补齐 ipv4.gateway 与 ipv4.dns，reapply 保留原 IP。DashScope HTTPS 返回 401（无凭据连通测试），NTP 已同步。回滚可将该连接 gateway、dns 恢复为空并 reapply。
- 用户明确授权配置 `pc` 免密 sudo：`/etc/sudoers.d/90-pc-nopasswd`；完整 sudoers 校验及 `sudo -n true` 通过。无需密码写入项目。

现场接入顺序（以下尚未执行）：

1. Key 已配置并验证；接入音频设备，确认 `voice/runtime/voice.env` 中设备名称与实际枚举匹配。不要将凭据提交到 Git。
2. 确认机器人所连网卡与地址后，设置 `voice/runtime/deployment.env` 的 `ROS_INTERFACE` 和 `NAV_IFACE` 为实际网卡；若为 eno1，两个值均为 eno1。此文件不会自动配置网卡 IP。
3. `systemctl --user restart voice-arm-bridge`，检查 `curl -s http://127.0.0.1:8765/healthz`。
4. 在现场安全条件满足后，依次启动 `voice-navigation`、`voice-arm-control`，再启动 `voice-tour-guide`，每一步检查状态与日志。
5. `curl -s http://127.0.0.1:8766/status` 必须显示真实预热就绪；再通过网页手动启动导览。离线桥正常不代表机器人在线或导览就绪。

常用检查：`systemctl --user status voice-control-console voice-arm-bridge`；`journalctl --user -u voice-arm-bridge -n 50`；网页端口 8081。

2026-10-21 新增根目录 `DEPLOY_GUIDE.md`（README 顶部有入口）：只写 Orin 用户级 systemd 路线，从 clone 到只读验证再到现场接入，分步给出命令、验证与常见失败；Orin 的 venv/torch 安装、JetPack 版本、`unitree_sdk2` 与模型缓存来源、正确 `libcusparseLt` 来源仍标“未确认”，需向维护者补齐后更新该文档。

## 项目整体描述

- 统一仓库路径：本机 `projects/RobotABrainOnline`，Orin NX `/home/unitree/RobotABrainOnline`；AGX `/mnt/ssd/navgation/projects/temp/unitree_slam_example_new` 仅为历史部署副本。
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
- `PROJECT_GUIDE.md`：面向初次接手者的详细架构、模块逻辑、接口、配置、状态机、诊断和开发验证指南。

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
- 2026-07-17 只读检查：五个服务均为 `disabled`；控制台、HTTP/ROS 桥、导航和手臂服务为 `active`，旧导览服务为 `failed`。本轮未改变这些运行状态。
- **未经用户明确授权，不得启动、重启或 enable 任何上述服务，也不得执行真实导航、重定位或手臂动作。**
- **不要直接运行 `voice/scripts/install_services.sh`：该脚本末尾包含 `systemctl enable --now voice-control-console.service voice-arm-bridge.service`，会启动服务。**
- 只更新 unit 时应手动复制对应 service 和 sudoers 文件，校验后执行 `systemctl daemon-reload`；不得擅自启动或 enable 服务。

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

- 路线加载曾丢弃 `post_wait_seconds`；现保留该字段并由 `voice_console/post_wait_runner.py` 在播报后统一等待，日志为 `[route] post-wait start/complete`。

## 导览语音打断并发竞态修复

- `voice_console/interrupt.py` 的 `RouteInterruptCoordinator` 以 attempt/session 绑定每次播放与打断，WAV 结束后保留 0.9 秒边界窗口；打断问题不入后台队列，问答完成后提示“如果没有别的问题，我将继续导览。”再从当前台词开头恢复；`play_audio_file()` 非零返回码抛 `AudioPlaybackError`。真实麦克风/扬声器边界仍待现场验证。

## Kokoro 男性音色

- 默认 `hexgrad/Kokoro-82M` / `zm_yunxi`（repo 与 voice 须同时切换），可用 `KOKORO_REPO_ID`、`KOKORO_VOICE` 覆盖；WAV 缓存键含模型、音色、语速和文本。

## 导览启动耗时分析

- 冷启动约 13–19 秒主要耗在 Torch/Kokoro 导入、CUDA 加载与 Pipeline 初始化；现 `voice-tour-guide.service` 常驻并监听 `127.0.0.1:8766`，“重启导览服务”负责预热，“启动/关闭导览”仅调用 `/start`、`/stop`。改路线、模型或音色后须重启导览服务。

## Kokoro 中文语速稳定修复

- Kokoro 中文长段超过 510 音素会被静默截断；现按中英文标点及实际音素数（上限 220）分块并插入短停顿，缓存版本 `zh-prosody-v2`，日志 `[tts] synthesis_ready`。

## 多台词文件热更新

- “点位台词热更新”下拉切换 `voice/conf/*.json`（排除 `*_route.json`），后端校验路径边界；启动导览或重启服务时把当前选择写入 `voice/runtime/active_dialogue.json`，派生路线、问答上下文与 `.bak` 不进 Git。

## dialogue_1 扩展功能段

- 台词段支持 `{"speak_during_navigation":true}`（边走边说，`false` 或省略为到达后播报）和 `{"background_command":"coffee_delivery_run"}`（异步调用 `voice/send_delivery_task.py run`）；旧步骤级边走边说字段仍兼容，前端加载后会显示为台词功能段；opening 的问候与欢迎台词合并为一次握手下的连续播报，两句结束后才自动释放手臂。
- 后台命令严格使用白名单且单次导览按 once key 去重；所有随台词触发的非 `release_arm` 手势会在该段语音播放完成后自动发送 `release_arm`，`release_after_seconds` 表示播完后的额外延迟；问答只选择相关导览上下文并仅为实时问题启用联网搜索，控制台展示问答与 STT/推理/TTS 耗时，运行时交互记录限制为最近 200 条且单条文本最多 500 字。

## 已验证事实

- 2026-07-28 新增 `PROJECT_GUIDE.md` 与根目录 `README.md`（NX 正式导览启动前检查、启动/停止顺序、日志与常见失败）。
- 迁移后控制台启动失败根因为 `.venv/bin/activate` 硬编码旧目录；脚本已改为直接调用 `${VENV_ROOT}/bin/python`。项目已从 NX `/home/unitree/gt` 迁到 `/home/unitree/RobotABrainOnline`。
- `docker run --rm unitree_slam_example_new:humble check` 成功，六个节点无缺失依赖；无宿主 SDK 也可用镜像编译；Compose、Shell、compileall 与 unit 静态检查通过。
- opening 合并路线确认 `dialogue_0/1` 均只握手一次，NX 全套离线测试 `84 passed`，未播放音频或发送机器人任务。
- Humble 临时订阅者已收到桥发布的 `/go_goal_pose`、`/current_pose` 跨容器可达；`goGoalNavigation eth0` 仅完成初始化，真实运动链尚未验证。

## Git 与同步状态

- 根目录是 `main` 分支 Git 仓库，`voice/` 由根仓库统一管理，不要在子目录再次执行 `git init`。
- 2026-07-30 已合并本机 GitHub `main` 的项目指南历史与 NX 后续功能历史；
- 2026-10-21 已将 `codex/new-orin-offline-deploy`（Orin 离线部署、无机器人调试、系统音频播放）合并入 `main`，无文本冲突；因 `main` 已有“手势台词播完自动 `release_arm`”规则，同步更新 `voice/tests/test_debug_no_robot.py` 的预期动作序列。本机 Python 3.9 临时环境 `89 passed, 5 skipped`，唯一失败为本机未装 `torch`；`test_app.py` 依赖 3.10+ 类型语法，在本机需 `eval_type_backport` 才能导入。合并前本机未提交的 README/PROJECT_GUIDE/HANDOFF 改动与 `docs/` 已按用户要求丢弃，备份于 `git stash`。后续每轮修改仍须更新本报告、创建中文提交，并确认本机与 NX 的 `HEAD` 一致。

## 剩余阻塞与下一步

1. 用 root 权限安装更新后的 `voice-tour-guide.service` 和控制台 sudoers、校验并 `daemon-reload`；当前仓库同步不会自动修改 `/etc`。
2. 在不启动机器人运动节点的前提下，验证常驻预热、无线麦克风采音、STT、Kokoro 和 REDMI 外放链路。
3. 只有在现场安全确认并获得用户明确授权后，才能验证任务启动时延、停止行为、导航、手臂 Action 和完整导览。
4. 不得在日志中记录 API Key、密码、完整识别文本或大体积 DDS 原始数据。
