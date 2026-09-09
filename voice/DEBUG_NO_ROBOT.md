# 机器人离线导览调试

入口：`voice/scripts/debug_tour_no_robot.sh`。这里的离线仅指机器人不在线；DashScope 语音识别和 Qwen 问答仍需联网与有效 Key。

脚本直接复用项目路线、播报、小智唤醒打断、问答和恢复逻辑。导航使用进程内模拟对象，手臂请求只打印 `[SIMULATION]`，不会创建 ROS 节点或访问 HTTP/ROS 桥，也不启动任何正式服务。不要同时运行正式导览，避免争抢麦克风。

## 启动

在 Orin 项目根目录运行：

```bash
# 默认经 PulseAudio 使用系统当前选择的音响
voice/scripts/debug_tour_no_robot.sh --check
voice/scripts/debug_tour_no_robot.sh

# 蓝牙音响：先在系统中配对、连接，并设为 PulseAudio 默认输出
# USB 只枚举为串口不代表已具备音频输出能力
pactl list short sinks
pactl set-default-sink <实际音响的sink名称>
voice/scripts/debug_tour_no_robot.sh --check
voice/scripts/debug_tour_no_robot.sh
```

默认输出统一为 `pulse`，USB/蓝牙音响由系统选择，不再按名称或声卡编号匹配。`SPEAKER_NAME` 不再决定播放出口；`APLAY_DEVICE` 或显式 `--aplay-device` 可覆盖默认值，直连 `plughw` 会绕过系统音频服务器，仅用于明确需要的高级调试。

默认麦克风为 DJI MIC MINI（`plughw:CARD=MINI,DEV=0`），以 48000 Hz 双声道打开，再由项目转换为 16000 Hz 单声道送识别。默认只运行第一个路线项（开场）；每次模拟行走 1 秒，台词后等待最多 2 秒。

开始播报后，说“小智，……”打断并提问；回答后复用原程序的恢复播报行为。按 Ctrl+C 停止并释放设备。正常播完所选点位后退出。Key 从 `voice/runtime/voice.env` 加载，不需要写在命令里。

```bash
# 从第 2 个路线项开始，运行 3 项
voice/scripts/debug_tour_no_robot.sh --start-point 2 --point-count 3

# 跑完整路线，保留正式配置中的全部等待时间
voice/scripts/debug_tour_no_robot.sh --point-count 0 --post-wait-limit -1

# 显式指定其他 ALSA 设备
voice/scripts/debug_tour_no_robot.sh --mic plughw:CARD=MINI,DEV=0 --aplay-device plughw:CARD=Speaker,DEV=0
```

`--dialogue` 可指定另一份 dialogue JSON；原正式路线不会被改写。临时路线与问答上下文放在 `voice/runtime/debug-tour-*`，退出后自动清理。生成的语音缓存复用 `voice/guide_cache`。`--check` 会短暂打开麦克风采集到 `/dev/null`，向所选输出发送 0.1 秒静音；它验证设备可打开，不证明现场听得见。

## 已验证与边界

- 模拟路线测试覆盖导航、手臂与播报路径，并禁止真实 RouteNode 和 HTTP 请求；取消操作可中断模拟等待。
- `null` 是 ALSA 静音输出，仅供自动化流程检查；`--aplay-device null` 通过不代表音响外放成功。
- REDMI 现已识别为 USB 音频设备并成为系统默认输出。默认播放已实测进入 PulseAudio 并路由到该音响，未改变系统音量。实际听感与人声打断完整交互仍需现场确认。麦克风已实测可录音并识别测试语句。
- 调试器保留项目现有语音日志风格；不要把可能包含问答内容的终端日志公开发布。
