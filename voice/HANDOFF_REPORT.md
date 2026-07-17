# HANDOFF_REPORT

## 项目整体描述

- NX 项目路径：`/home/unitree/RobotABrainOnline/voice`；迁移来源为 AGX 的 `/mnt/ssd/pc/projects/voice`，现为 `RobotABrainOnline` 根 Git 仓库的子目录。
- 用途：为 Unitree G1 提供复星在线多点导览、实时语音识别、Qwen 问答、Kokoro 中文语音合成和 8081 前端控制台。
- 技术栈：NX 宿主 Python 3.8 venv、FastAPI/Uvicorn、DashScope/OpenAI 兼容接口、Kokoro/PyTorch CUDA、ALSA；所有 ROS 2 功能均在 Ubuntu 22.04/ROS 2 Humble Docker 中运行。
- 架构硬约束：宿主 venv 不 source Foxy、不 import `rclpy`、不安装 `custom_action_interfaces`。宿主只通过 `127.0.0.1:8765` HTTP 访问 Humble 桥。

## 主要模块与数据流

- `voice_console/`：8081 网页、REST API、配置读写、systemd 控制和 HTTP 位姿轮询。
- `voice_console/supervisor.py`：监督路线程序，维护脱敏状态，不记录识别原文。
- `nav_route_then_speak.py`：宿主 venv 中的导览编排、STT、Qwen、Kokoro；通过 Humble HTTP 桥发导航目标、等导航状态、触发手臂动作。
- `arm_action_bridge.py`：仅在 Humble 容器运行；桥接 `/go_goal_pose`、`/nav_status`、`/current_pose` 和 `/navi_arm`。
- `docker-compose.bridge.yml`：复用 `unitree_slam_example_new:humble`，host network/IPC，只监听宿主回环端口 8765。
- `deploy/fastdds-eth0.xml`：Fast DDS UDP 白名单模板；启动时按机器人网卡地址生成 `runtime/fastdds.xml`，避免双网卡/容器 SHM 导致“发现但无数据”。
- `conf/dialogue_0.json`：标准导览配置；派生 `conf/dialogue_0_route.json` 和 `guide_qa_context.txt`。
- `guide_cache/`：既有播报缓存；`hf_cache/`（313 MB，未纳入源码副本）为 `hexgrad/Kokoro-82M-v1.1-zh` 离线模型缓存。
- `scripts/install_nx_venv.sh`：NX venv 可复现安装入口，包含 JetPack 5.1.1 PyTorch 和 Python 3.8/Kokoro 0.9.4 兼容处理。

核心数据流：

1. 浏览器访问 `http://172.16.2.156:8081`，控制台管理 systemd 服务和导览配置。
2. 导览、DashScope/Qwen 请求和 Kokoro CUDA 合成都在宿主 venv 运行，避免额外容器网络边界。
3. 导览通过 localhost HTTP 把目标/动作交给 Humble 桥；桥使用 Humble ROS topic/action 与导航、手臂节点通信。
4. Humble 导航/手臂节点再经 NX `eth0` 和 Unitree SDK/DDS 访问机器人 PC1。

## 重要路径、配置与服务

- venv：`/home/unitree/RobotABrainOnline/voice/.venv`；主 Humble 项目：`/home/unitree/RobotABrainOnline`。
- API 配置：`runtime/voice.env`，权限 0600；不得输出其内容。启动脚本用 `set -a` 导出变量。
- 地图字符串：`/home/unitree/test7.pcd`，由 PC1 `192.168.123.161` 的 SLAM 服务解析，不挂载进 NX/Docker。
- 五个 `voice-*` systemd unit 均已安装，但按当前要求保持 `disabled/inactive`，防止无人值守启动控制台、桥或运动节点。

常用命令：

```bash
cd /home/unitree/RobotABrainOnline/voice
sudo ./scripts/install_services.sh
systemctl status voice-control-console voice-arm-bridge
curl -s http://127.0.0.1:8081/api/status
curl -s http://127.0.0.1:8765/healthz
sudo systemctl start voice-navigation voice-arm-control
sudo systemctl start voice-tour-guide
journalctl -u voice-tour-guide -n 100 --no-pager
```

## 当前状态与已验证事实

- NX `wlan0=172.16.2.156/24` 负责公网，`eth0=192.168.123.164/24` 负责机器人网；PC1 `192.168.123.161` 可达。
- 宿主控制台进程环境中 `ROS_DISTRO`、`AMENT_PREFIX_PATH` 均为空；无 Foxy 依赖时可导入并实例化路线 HTTP 客户端。
- Foxy RMW 在该 NX 上创建节点会出现 `bad_alloc`/段错误；旧方案曾使控制台占用约 14.8 GB。移除宿主 ROS 后控制台约 45 MB，问题已规避。
- Humble 桥容器成功解析镜像内 `custom_action_interfaces/action/NaviArm`；GET 健康、导航状态、位姿以及非法 Action 400 校验通过。
- Fast DDS 已固定到机器人网卡 UDP；使用临时 Humble 订阅者验证 HTTP `/goal` 发布可收到，临时 Humble 位姿发布可同时到达桥和 8081 控制台。未启动真实导航节点，因此未驱动机器人。
- NX venv 已安装 NVIDIA JetPack 5.1.1 PyTorch `2.0.0+nv23.05`，`torch.cuda.is_available()` 为 true。
- Kokoro 0.9.4 使用迁移的离线缓存成功在 CUDA 合成 57,600 个采样（WAV 115,244 bytes）。
- Qwen 在宿主 venv 通过 DashScope 实际调用成功，最小探测耗时约 0.637 秒；未记录 Key 或响应正文。
- 自动化测试：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests` 为 `23 passed`。
- 点位台词的 `post_wait_seconds` 已在 `load_speech_segments()` 中保留，不再于路线加载阶段丢失；点位8的 277 秒配置和 0.05 秒短等待链路验证通过。
- systemd unit、Compose、Shell 语法、Python compileall 均验证通过；桥服务重启只保留一个受 Compose 管理的容器。

## 剩余阻塞与下一步

- NX 已枚举无线麦克风 `plughw:CARD=Rx,DEV=0`（ALSA card 2）和 `REDMI Speaker 2-6002` 对应的 `plughw:CARD=R26002,DEV=0`（ALSA card 3）；真实 STT 采音和外放仍待端到端验证。
- 未经现场安全确认，尚未启动真实导航、重定位、手臂 Action 或完整导览。需先接好音频设备，再依次启动导航、手臂，最后启动导览。
- Fast DDS 模板依赖机器人网卡 IPv4；桥默认接口为 `eth0`，可用 `ROS_INTERFACE` 覆盖。主项目运行脚本会从传入网卡或 `192.168.123.0/24` 自动识别。
- NX 到 GitHub/Hugging Face 可能超时；模型已离线迁移。重建 venv 时 spaCy 英文模型若下载失败，应从联网机器提供 `SPACY_MODEL_WHEEL`。
- 不得在日志中记录 API Key、密码、完整识别文本或大体积 DDS 原始数据。

## 版本管理

- `voice/` 由 `RobotABrainOnline` 根 Git 仓库统一管理，不要在本目录再次执行 `git init`；运行时密钥、venv、模型缓存、日志和离线 wheel 均被忽略。
