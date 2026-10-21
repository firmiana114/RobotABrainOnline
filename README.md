# RobotABrainOnline

> 在新的 Jetson Orin 上部署，请阅读 [本项目 Orin 部署指南](DEPLOY_GUIDE.md)：根据 Codex 部署历史整理，包含环境准备、安装命令、分层验收和仍需备齐的材料。Orin 使用用户级 systemd；下文 NX 启动命令不能直接套用。

RobotABrainOnline 是 Unitree G1 的在线导览项目，整合 ROS 2 Humble 导航、手臂动作、网页控制台、阿里云语音识别与 Qwen 问答，以及 Kokoro 中文语音合成。

本文只说明 NX 上已经完成部署后的正式导览启动方法。正式导览会向机器人发送导航目标和手臂动作；执行前必须确认现场安全并获得操作授权。

## 部署位置与组成

- NX 项目目录：`/home/unitree/RobotABrainOnline`
- 控制台地址：`http://<NX-IP>:8081`
- 机器人通信网卡：默认 `eth0`
- 正式路线配置：`voice/conf/dialogue_0.json`
- 运行时私密配置：`voice/runtime/voice.env`
- 默认 Kokoro 模型与音色：`hexgrad/Kokoro-82M` / `zm_yunxi`

正式导览由以下五个 systemd 服务组成：

| 服务 | 用途 |
| --- | --- |
| `voice-control-console.service` | 提供 8081 网页控制台和服务管理接口 |
| `voice-arm-bridge.service` | 在 Humble 容器内提供导航、位姿和手臂 HTTP/ROS 桥 |
| `voice-navigation.service` | 启动 `goGoalNavigation66` 导航节点 |
| `voice-arm-control.service` | 启动 G1 手臂 Action Server |
| `voice-tour-guide.service` | 常驻预热模型、路线语音、STT 和导航订阅；按需执行正式导览任务 |

## 启动前检查

连接 NX 后进入项目目录：

```bash
ssh NX
cd /home/unitree/RobotABrainOnline
```

确认代码、关键运行环境和设备：

```bash
git status --short
git rev-parse --short HEAD
test -x voice/.venv/bin/python
test -s voice/runtime/voice.env
docker image inspect unitree_slam_example_new:humble >/dev/null
ip -4 addr show eth0
arecord -l
aplay -l
```

启动前还必须人工确认：

1. 机器人、NX 和 PC1 网络连接正常，`eth0` 是机器人通信网卡。
2. 导航地图及路线点位已经现场校验，周围没有人员或障碍物侵入运动区域。
3. 手臂活动范围安全，急停可用，并有现场人员监护。
4. `voice/runtime/voice.env` 已配置有效的 `DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY`。不要在终端或日志中输出密钥内容。
5. 无线麦克风和 REDMI 扬声器已连接；默认设备分别为 `plughw:CARD=Rx,DEV=0` 和 `REDMI Speaker 2-6002`。

## 推荐：通过网页控制台启动

先启动底层桥和控制台：

```bash
sudo systemctl start voice-arm-bridge.service
sudo systemctl start voice-control-console.service
systemctl is-active voice-arm-bridge.service voice-control-console.service
```

浏览器打开：

```text
http://<NX-IP>:8081
```

在控制台中严格按以下顺序操作：

1. 点击“启动导航”，等待导航状态正常。
2. 点击“重启手臂控制”，等待手臂控制状态正常。
3. 点击“重启导览服务”，等待导览任务状态显示“已预热”。这一步会重启 Python 进程，并完成 Kokoro/CUDA、整条路线语音缓存、STT 和导航订阅预热，不会开始路线或发送机器人动作。
4. 再次确认现场安全后，点击“启动导览”。此按钮只启动已预热进程中的导览任务，不再重启 Python 进程。

控制台只有在导航、手臂控制和导览预热均已就绪时才允许启动导览。导览启动后会重新读取正式路线并发送真实导航目标，到达点位后执行配置的手臂动作和语音播报。“关闭导览”只停止当前导览任务并保留预热进程，因此下一次“启动导览”无需再次加载模型。

修改路线文本、音色、模型或运行时语音配置后，应再次点击“重启导览服务”，使整条路线的语音缓存与新配置一致；否则任务启动时可能需要临时合成新增台词。

## 命令行启动

不使用网页按钮时，按顺序逐项启动并检查状态。不要把以下命令合并成后台批量启动命令：

```bash
sudo systemctl start voice-arm-bridge.service
systemctl is-active voice-arm-bridge.service

sudo systemctl start voice-navigation.service
systemctl is-active voice-navigation.service

sudo systemctl start voice-arm-control.service
systemctl is-active voice-arm-control.service

sudo systemctl restart voice-tour-guide.service
systemctl is-active voice-tour-guide.service
```

等待本机控制接口返回 `ready` 或 `stopped` 后再启动任务：

```bash
curl -s http://127.0.0.1:8766/status
curl -sS -X POST http://127.0.0.1:8766/start
```

如需同时查看网页状态，再启动控制台：

```bash
sudo systemctl start voice-control-console.service
```

不要跳过导航和手臂控制直接重启 `voice-tour-guide.service`。该服务虽然会通过 `Requires=` 拉起 HTTP/ROS 桥，但不会自动启动导航和手臂节点；缺少 `/go_goal_pose` 订阅者时预热会失败。`systemctl restart` 只建立常驻预热进程，只有控制接口的 `/start` 或网页“启动导览”才会开始真实运动流程。

## 查看状态和日志

查看全部服务状态：

```bash
systemctl --no-pager --full status \
  voice-control-console.service \
  voice-arm-bridge.service \
  voice-navigation.service \
  voice-arm-control.service \
  voice-tour-guide.service
```

实时查看正式导览日志：

```bash
journalctl -fu voice-tour-guide.service
```

查看本次启动以来的各组件日志：

```bash
journalctl -b -u voice-tour-guide.service --no-pager
journalctl -b -u voice-navigation.service --no-pager
journalctl -b -u voice-arm-control.service --no-pager
journalctl -b -u voice-arm-bridge.service --no-pager
journalctl -b -u voice-control-console.service --no-pager
```

导览服务重启时会导入 Torch/Kokoro、把模型加载到 CUDA、初始化 Pipeline、执行首次推理，并预生成整条路线的语音缓存。旧模型冷启动实测约 18 秒，当前 `Kokoro-82M / zm_yunxi` 在缓存完备后的离线冷进程实测约 13.25 秒；首次使用新语速分块版本或修改路线后，还需等待全部台词重新合成。日志中的 `[startup]` 会记录 `voice_ready`、`guide_cache_ready`、`stt_ready` 和 `nav_subscriber_ready` 的阶段与累计耗时，`[tts]` 会记录不含台词正文的分块指标；这些都发生在预热期，不会发送导航或手臂动作。状态进入“已预热”后，“启动导览”只触发任务，避免重复支付进程冷启动成本。

日志中不应粘贴或记录 API Key、完整识别文本、完整问题内容或大体积音频数据。

## 停止正式导览

正常停止时先通过网页“关闭导览”或本机接口停止当前任务；常驻预热进程会保留，之后可立即再次启动：

```bash
curl -sS -X POST http://127.0.0.1:8766/stop
```

确定不再使用正式导览时，再停止常驻服务和运动节点：

```bash
sudo systemctl stop voice-tour-guide.service
sudo systemctl stop voice-navigation.service
sudo systemctl stop voice-arm-control.service
sudo systemctl stop voice-arm-bridge.service
```

不再使用网页控制台时可一并停止：

```bash
sudo systemctl stop voice-control-console.service
```

仅停止 `voice-tour-guide.service` 不会自动停止导航和手臂控制服务。出现现场风险时应优先使用机器人急停，不能等待软件流程正常退出。

## 常见启动失败

- `Missing DASHSCOPE_API_KEY or OPENAI_API_KEY`：检查 `voice/runtime/voice.env` 是否存在有效密钥及读取权限。
- `no subscriber on /go_goal_pose`：导航节点尚未就绪；检查 `voice-navigation.service` 和桥接日志。
- 麦克风启动失败：检查 `arecord -l` 中是否存在 `Rx`，以及 `ALSA_INPUT_DEVICE`、通道数和采样率配置。
- 扬声器播放失败：检查 `aplay -l` 中的 REDMI 设备，并查看导览日志里的设备名和 `aplay` 返回码。
- 服务进入 `failed`：先通过 `journalctl -b -u <服务名>` 查明原因；修复后执行 `sudo systemctl reset-failed <服务名>` 再重新启动。
- 导览服务为 `active` 但“启动导览”不可用：预热可能尚未完成；查看控制台任务状态或 `curl -s http://127.0.0.1:8766/status`，并检查 `[startup]` 日志。
- 日志显示从旧目录或系统 Python 启动：当前脚本应直接使用 `voice/.venv/bin/python`；确认 NX 已同步到最新提交，避免加载迁移前虚拟环境中的硬编码路径。

首次接触项目时，请先阅读 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)，其中详细说明系统边界、模块职责、核心数据流、配置生成、导览状态机、接口和开发验证方法。

近期部署状态、验证记录和现场未验证事项见 [HANDOFF_REPORT.md](HANDOFF_REPORT.md)。
