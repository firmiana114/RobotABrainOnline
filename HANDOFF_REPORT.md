# HANDOFF_REPORT

## new-orin 离线部署（2026-09-09 检查）

此节描述 `new-orin`，与下文历史 NX 部署分开。路径为 `/mnt/disk1/gt/RobotABrainOnline`，账号为 `pc`。

- 使用用户级 systemd，所有服务操作必须加 `--user`。五个 unit 已安装；控制台与 HTTP/ROS 桥 active、enabled；导航、手臂、导览 inactive、disabled。用户 Linger=yes。
- `python3 voice/scripts/install_user_services.py` 可重新生成适配当前目录的 unit，不启动服务，不覆盖已有运行时配置。控制台通过 `voice/runtime/systemctl-user` 管理用户级服务。
- 系统 CUDA 的 `libcusparseLt.so.0` 错误链接到了 `libcusparse.so`。本次未改系统库，服务通过 `LD_LIBRARY_PATH` 优先使用旁目录 `../RobotABrainOnline-runtime/lib` 的正确库。Torch CUDA 运算通过；`pip check` 无冲突。
- `voice/scripts/prewarm_offline.sh` 使用正式分块合成和缓存逻辑，无需 Key，不创建云客户端、不录音、不播放、不调用机器人。真实 CUDA 合成 34 段，均可读取、采样率 24000 Hz、数据有限，总时长 496 秒；另执行一次强制新合成探针。运行日志与派生路线位于 `voice/runtime/`，不进入 Git。
- 镜像 `unitree_slam_example_new:humble` 的离线 check 通过，SDK、自定义 Action、六个节点及动态库完整。桥 `/healthz` 返回 ok，`/nav_state` 的订阅者为 0，与机器人离线一致；控制台首页 HTTP 200。
- 原有测试 66 passed；新增离线模式测试 1 passed，验证无 Key 时离线模式不创建云客户端，正式模式仍拒绝无 Key。
- `voice/runtime/deployment.env` 当前 `ROS_INTERFACE=lo`，桥只使用回环接口。现场机器人候选有线口 `eno1` 当前无链路；未修改 IP、路由，未启动运动节点。
- `voice/runtime/voice.env` 已创建（0600），Key 留空。云端 STT/Qwen 真实调用尚未验证；未检测到文档指定的 Rx 麦克风及 REDMI 扬声器，实际采集和外放待设备连接。
- 公网修复：同网段本机 DHCP 确认路由器/DNS 为 192.168.101.254；为 Tsinghua-WX-5G 补齐 ipv4.gateway 与 ipv4.dns，reapply 保留原 IP。DashScope HTTPS 返回 401（无凭据连通测试），NTP 已同步。回滚可将该连接 gateway、dns 恢复为空并 reapply。
- 用户明确授权配置 `pc` 免密 sudo：`/etc/sudoers.d/90-pc-nopasswd`；完整 sudoers 校验及 `sudo -n true` 通过。无需密码写入项目。

现场接入顺序（以下尚未执行）：

1. 在 `voice/runtime/voice.env` 本机填入有效 Key，确认音频设备名称。不要将凭据提交到 Git。
2. 确认机器人所连网卡与地址后，设置 `voice/runtime/deployment.env` 的 `ROS_INTERFACE` 和 `NAV_IFACE` 为实际网卡；若为 eno1，两个值均为 eno1。此文件不会自动配置网卡 IP。
3. `systemctl --user restart voice-arm-bridge`，检查 `curl -s http://127.0.0.1:8765/healthz`。
4. 在现场安全条件满足后，依次启动 `voice-navigation`、`voice-arm-control`，再启动 `voice-tour-guide`，每一步检查状态与日志。
5. `curl -s http://127.0.0.1:8766/status` 必须显示真实预热就绪；再通过网页手动启动导览。离线桥正常不代表机器人在线或导览就绪。

常用检查：`systemctl --user status voice-control-console voice-arm-bridge`；`journalctl --user -u voice-arm-bridge -n 50`；网页端口 8081。

本次保留原有 README.md、PROJECT_GUIDE.md、voice/voice_console/app.py 修改及 backups、agent_orchestration.json；不提交这些既有改动。


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
- 打断问答成功播放完毕后，会先播报“如果没有别的问题，我将继续导览。”，提示结束后才从当前台词开头恢复；空闲问答、继续命令、无问题超时和回答失败分支不播放该提示。
- `play_audio_file()` 检查 `aplay` 返回码并区分正常完成、主动打断和播放失败。非零退出或启动/终止异常会抛出保留异常链的 `AudioPlaybackError`，不再被当作台词成功完成。
- 新增 INFO/ERROR 诊断日志：attempt/session 生命周期、边界归属、问题片段数量与长度、回答阶段、音频设备、文件名和返回码；不记录识别全文、问题全文、密钥或音频数据。
- 本机全量测试为 `32 passed, 2 skipped`，跳过项依赖本机未安装的 DashScope/完整语音栈；新增纯并发测试连续运行 10 轮均通过。NX Python 3.8 环境候选副本全量测试为 `34 passed`。
- 未启动导航、手臂或完整导览，未发送机器人动作。真实麦克风、扬声器、慢 Qwen 网络和 WAV 自然结束边界仍需现场安全条件下验证。

## Kokoro 男性音色

- 当前默认模型与音色为官方 `hexgrad/Kokoro-82M` / `zm_yunxi`；`zm_yunxi` 不属于先前的 `Kokoro-82M-v1.1-zh` 数字编号音色，因此切换时必须同时更换 repo 和 voice。
- `voice_console.config` 统一维护默认 repo 和 voice；导览 supervisor 显式传入两者，并允许分别通过 `KOKORO_REPO_ID`、`KOKORO_VOICE` 覆盖。
- 导览 WAV 缓存键包含模型、voice、语速和文本，旧 `zf_001`、`zm_029`、`zm_009` 缓存不会被新音色误用；新增运行时 WAV 已加入忽略规则，已有预生成缓存仍由 Git 保留。
- NX 离线缓存已补齐通用模型和 `zm_yunxi` 音色；CUDA 内存合成生成 56,400 个采样点，未落盘或播放音频，也未启动正式导览或机器人节点。

## 导览启动耗时分析

- 2026-07-17 最近两次正式导览从 systemd 启动到进入首点分别为约 19.44 秒和 19.79 秒；路线派生只占约 0.08 秒，首点 `opening` 跳过导航，桥当时已有 2 个目标订阅者。
- NX 离线分段计时总计 18.00 秒：Torch/Kokoro 导入 4.16 秒、模型加载到 CUDA 3.88 秒、Pipeline 初始化 7.27 秒、首次推理预热 2.70 秒。当前每次启动都创建新导览进程，所以这笔成本会重复发生。
- 切换并补齐 `Kokoro-82M / zm_yunxi` 缓存后，离线冷进程总计 13.25 秒：导入 4.08 秒、模型与 CUDA 3.90 秒、Pipeline 2.60 秒、预热 2.66 秒；预计比旧模型少约 4.75 秒，但仍无法消除进程级冷启动。
- 现改为两层生命周期：`voice-tour-guide.service` 常驻 Python 进程并监听回环地址 `127.0.0.1:8766`；“重启导览服务”负责冷启动和全部预热，“启动/关闭导览”只通过 `/start`、`/stop` 控制任务，复用模型、STT 和导航订阅。
- 服务预热会生成整条当前路线的 Kokoro WAV 缓存；每次任务启动仍重新读取路线和问答上下文。修改路线、模型、音色或语音配置后应重启导览服务，保证任务启动前缓存完备。
- 任务取消覆盖导航等待、台词播放和 `post_wait_seconds`；不可打断的问答回复策略保持不变，回复完成后才处理停止。旧实现也无法撤回已提交给导航节点的目标，本轮未扩大该能力。
- 新增 `[startup] guide_cache_ready`、`[control] tour_starting/tour_stopping` 和任务状态日志；只记录阶段、耗时、段数和状态，不记录识别全文、问题全文、密钥或音频数据。
- Python 3.8 候选副本全量模拟测试为 `61 passed`，覆盖常驻控制接口、配置重载、路线预缓存、取消路径、重复启动抑制，以及原有并发打断回归；未启动真实导览或发送机器人动作。

## Kokoro 中文语速稳定修复

- 根因：Kokoro 0.9.4 的中文路径只按 ASCII `. ! ?` 分句，不识别中文标点；长段直接推理时音素超过 510 会被静默截断。原路线最长段为 185 字/747 音素，只保留 68.3%，旧 WAV 仅 18.275 秒并呈现明显节奏压缩。
- 合成前现按中英文标点拆分，并依据实际 G2P 音素数二次切分；每块上限 220，显著低于 510 硬限制。无法安全切分或未生成音频时直接失败，不再接受截断结果。
- 分块间按逗号、分号、句号加入 0.12/0.20/0.28 秒静音；缓存版本升级为 `zh-prosody-v2`，重启预热时会重建路线缓存，不复用旧异常 WAV。
- 新增 `[tts] synthesis_ready` INFO 诊断，记录字符数、分块数、最大音素数、停顿总时长和采样数，不记录台词全文。
- NX 离线 CUDA 验证原异常段变为 4 块、最大 215 音素、37.670 秒和约 4.91 字/秒；全路线 34 段拆为 65 块，最大 216 音素，无块接近硬限制。候选副本全量模拟测试 `66 passed`；验证 WAV 已删除，未播放音频或发送机器人动作。

## 已验证事实

- 2026-07-28 新增 `PROJECT_GUIDE.md`，并从根目录 `README.md` 提供入口；文档依据当前 Python、C++、Docker、systemd、配置和测试实现整理。
- 控制台启动失败根因是迁移后的 `.venv/bin/activate` 仍硬编码旧目录 `/home/unitree/gt/voice/.venv`，使 systemd 最终调用缺少 `uvicorn` 的 `/usr/bin/python3`；控制台、导航和导览脚本已改为直接调用可迁移的 `${VENV_ROOT}/bin/python`，并记录实际解释器路径。
- 根目录新增 `README.md`，集中说明 NX 正式导览的启动前安全检查、网页/命令行启动顺序、状态与日志查看、停止顺序和常见失败；不包含纯语音试听流程。
- `docker run --rm unitree_slam_example_new:humble check` 成功；六个节点 ELF 的 `ldd` 无缺失依赖，RUNPATH 指向镜像内 SDK 和自定义 Action。
- 无宿主 SDK 上下文仍可使用既有镜像编译 `custom_action_ws` 和六个节点。
- Compose、Shell 语法、Python compileall 和 systemd unit 静态检查通过。
- 提交前最终 NX 主工作区测试结果应以本轮 Git 与同步步骤记录为准；候选副本当前为 `61 passed`。
- Humble 临时订阅者已收到 HTTP 桥发布的 `/go_goal_pose`；临时 `/current_pose` 已到达桥和控制台，验证跨容器 ROS 数据链但未驱动机器人。
- `goGoalNavigation eth0` 曾只完成 ROS/Unitree 初始化并保持 8 秒，未发送目标或启动重定位；真实运动链尚未验证。
- 项目已从 NX `/home/unitree/gt` 原地迁移到 `/home/unitree/RobotABrainOnline`，旧路径已移除。

## Git 与同步状态

- 根目录是 `main` 分支 Git 仓库，`voice/` 由根仓库统一管理，不要在子目录再次执行 `git init`。
- 2026-07-28 编写项目指南时，本机连接 NX `172.16.2.156:22` 超时，无法核对或同步 NX；服务器当前 HEAD 和工作区状态未确认。
- 2026-07-17 合并交接报告前，本机与 NX 已验证处于相同基线提交 `9eb2a5a342545e3de3d7b83cb4b3659f7977f491`，两端工作区均干净。
- 本报告本身会产生后续提交，因此当前部署提交应以两端 `git rev-parse HEAD` 的实际结果为准；每次修改后必须再次确认本机与 NX HEAD 相同且工作区干净。
- 修改代码或文档后必须更新本报告、创建中文 Git 提交，并把 NX 同步到同一提交。

## 剩余阻塞与下一步

1. 用 root 权限安装更新后的 `voice-tour-guide.service` 和控制台 sudoers、校验并 `daemon-reload`；当前仓库同步不会自动修改 `/etc`。
2. 在不启动机器人运动节点的前提下，验证常驻预热、无线麦克风采音、STT、Kokoro 和 REDMI 外放链路。
3. 只有在现场安全确认并获得用户明确授权后，才能验证任务启动时延、停止行为、导航、手臂 Action 和完整导览。
4. 不得在日志中记录 API Key、密码、完整识别文本或大体积 DDS 原始数据。
