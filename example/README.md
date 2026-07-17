# unitree_slam_example / example 使用说明

## Docker 自包含 SDK 环境

当前 `unitree_slam_example_new:humble` 镜像已内置 Unitree SDK2、`custom_action_ws` 的安装产物
和六个已编译节点，运行期不再依赖宿主机 SDK 或源码挂载。可先执行不连接机器人的安全自检：

```bash
docker run --rm unitree_slam_example_new:humble check
```

Humble 容器默认固定使用 `rmw_fastrtps_cpp`，避免继承 NX 宿主机 Foxy 的 CycloneDDS 设置。
如确需覆盖，应使用 `HUMBLE_RMW_IMPLEMENTATION`，不要直接复用宿主机的
`RMW_IMPLEMENTATION`。ROS 2 日志默认写入 `/tmp/unitree-slam-ros/log`。

仅使用镜像运行节点的形式如下；网卡参数必须按目标机器替换：

```bash
docker run --rm --network host --ipc host --privileged \
  unitree_slam_example_new:humble run <target> <iface> [args...]
```

如果目标机器同时有源码，仍可重新编译开发版本：

```bash
./docker/build_example.sh
```

`build_example.sh` 只使用现有镜像，不会重新构建镜像。需要重新生成镜像时，显式执行：

```bash
UNITREE_SDK2_CONTEXT=/path/to/unitree_sdk2 ./docker/build_image.sh
```

镜像内 SDK 固定路径为 `/opt/unitree_sdk2`，自定义 Action 安装在
`/opt/unitree_slam/custom_action_ws`，六个节点安装在 `/opt/unitree_slam/nodes/bin`。
运行节点仍需使用 host 网络，并传入机器人网卡名；
AGX 使用 `eno1`，Orin NX 使用 `eth0`。地图参数如 `/home/unitree/test7.pcd` 由 PC1 的远端
SLAM 服务解析，不要求该文件存在于容器或 Orin NX 本地。

# 新建终端一定要先
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

本文档按 `example/src` 当前代码逐个说明：
- 可执行程序如何启动
- 每个程序的 ROS2 / Unitree DDS 话题
- 可直接复制的发布示例

## 1. 编译

```bash
cd /mnt/disk1/unitree_slam_example/example
cmake -S . -B build
cmake --build build -j4
```

> 网卡参数请替换为你的实际接口，如 `eno1` / `eth0`。

---

## 2. `src` 下程序清单

当前 `src` 下文件：
- `keyDemo.cpp`
- `goGoalNavigation_ros2.cpp`
- `gesture_topic_bridge.cpp`
- `g1_arm7__diy_action_example.cpp`
- `integrated_launcher.cpp`
- `inspire_g1.cpp`（当前文件为空）
- `actions/g1_actions.cpp`（动作库源码，不是独立程序）

---

## 3. 各程序运行与话题

## 3.1 `keyDemo.cpp` → `build/keyDemo`

键盘交互版 SLAM 示例（非 ROS2 话题交互）。

启动：

```bash
./build/keyDemo eno1
```

内部使用的 Unitree DDS 订阅：
- `rt/slam_info`
- `rt/slam_key_info`

键位功能（程序启动后终端提示）：
- `q`：开始建图
- `w`：结束建图
- `a`：开始重定位
- `s`：记录当前点位
- `d`：循环执行点位
- `f`：清空点位
- `z`：暂停导航
- `x`：恢复导航

---

## 3.2 `goGoalNavigation_ros2.cpp` → `build/goGoalNavigation`

ROS2 导航节点，接收目标和 SLAM 指令并调用 Unitree SLAM API。

启动：

```bash
./build/goGoalNavigation eno1
```

或带自动重定位地图：

```bash
./build/goGoalNavigation eno1 /home/unitree/test3.pcd --nav_speed=0.6
```

ROS2 订阅：
- `/go_goal_pose` (`std_msgs/msg/String`)
- `/slam_cmd` (`std_msgs/msg/String`)

ROS2 发布：
- `/nav_status` (`std_msgs/msg/String`)
- `/current_pose` (`std_msgs/msg/String`)

发布示例：

```bash
# 发送目标点（JSON 字符串）
ros2 topic pub --once /go_goal_pose std_msgs/msg/String \
"{data: '{\"x\":1.0,\"y\":0.5,\"z\":0.0,\"ox\":0.0,\"oy\":0.0,\"oz\":0.0,\"ow\":1.0,\"mode\":1}'}"
```

```bash
# 开始重定位
ros2 topic pub --once /slam_cmd std_msgs/msg/String \
"{data: '{\"cmd\":\"start_relocation\",\"pcd\":\"/home/unitree/test1.pcd\"}'}"
```

```bash
# 暂停导航
ros2 topic pub --once /slam_cmd std_msgs/msg/String "{data: '{\"cmd\":\"pause_nav\"}'}"
```

---

## 3.3 `gesture_topic_bridge.cpp` → `build/gestureTopicBridge`

手势桥接程序：
- ROS2 收命令
- 转发到 Unitree Inspire 手部 DDS 控制

启动：

```bash
cd /mnt/disk1/dfx_inspire_service/build
sudo ./inspire_g1
```

```bash
./build/gestureTopicBridge eno1
```

ROS2 订阅：
- `/gesture_cmd` (`std_msgs/msg/String`)

内部 Unitree DDS：
- 发布：`rt/inspire/cmd`
- 订阅：`rt/inspire/state`

命令格式：

```text
<gesture> [left|right|both] [duration_ms] [hold_ms]
```

动作编号（当前）
    "open"
    "close"
    "half"

    // 常见手势（可按实际硬件效果再微调）
    "ok"
    "victory"// 比耶
    "point"
    "thumbs_up"

    // 数字手势（按常见单手表达，可按实际效果微调）
    "num1" // 食指伸直
    "num2" // 食指+中指
    "num3" // 食指+中指+无名指
    "num4"

示例：

```bash
ros2 topic pub --once /gesture_cmd std_msgs/msg/String "{data: 'num3 right 800 2000'}"
```

```bash
ros2 topic pub --once /gesture_cmd std_msgs/msg/String "{data: 'victory both 800 0'}"
```

---

## 3.4 `g1_arm7__diy_action_example.cpp` → `build/g1Arm7DIYActionExample`

G1 双臂 7 自由度动作执行节点。

启动：

```bash
./build/g1Arm7DIYActionExample eno1
```

ROS2 订阅：
- `/g1_arm/action_cmd` (`std_msgs/msg/String`)

内部 Unitree DDS：
- 发布：`rt/arm_sdk`
- 订阅：`rt/lowstate`

支持 payload（字符串）：
- `0` / `release` / `release_init_pose` / `release_init_pos1`
- `1`~`12`
- 或动作名

动作编号（当前）：
- `1` `right_hand_handshake_ready`
- `2` `left_hand_self_introduction`
- `3` `left_hand_handshake_ready`
- `4` `right_hand_self_introduction`
- `5` `right_hand_handshake_wrist`
- `6` `both_hand_handshake_ready`
- `7` `left_arm_horizontal`
- `8` `right_arm_horizontal`
- `9` `left_wrist_outside`
- `10` `right_wrist_outside`
- `11` `both_arms_horizontal`
- `12` `both_arms_lower_horizontal`

发布示例：

```bash
ros2 topic pub --once /g1_arm/action_cmd std_msgs/msg/String "{data: '5'}"
```

```bash
ros2 topic pub --once /g1_arm/action_cmd std_msgs/msg/String "{data: 'both_arms_horizontal'}"
```

```bash
ros2 topic pub --once /g1_arm/action_cmd std_msgs/msg/String "{data: 'release'}"
```

> 说明：当前逻辑下，单臂动作执行时会自动释放对侧手臂到 `init_pos1`。

---

## 3.5 `integrated_launcher.cpp` → `build/integratedLauncher`

一个启动器程序，统一拉起 3 个子进程：
1. `goGoalNavigation`
2. `gestureTopicBridge`
3. `g1Arm7DIYActionExample`

启动：

```bash
./build/integratedLauncher eno1
```

或带地图：

```bash
./build/integratedLauncher eno1 /home/unitree/test1.pcd
```

说明：
- 这是 **多进程托管**（不是单进程融合）
- 启动器会打印每个子进程 PID 与退出信息

---

## 3.6 `inspire_g1.cpp` → `build/inspireG1`

当前 `src/inspire_g1.cpp` 文件为空，暂无可用逻辑。

- 若你后续补充该文件内容，再执行：

```bash
cmake --build build --target inspireG1 -j4
```

---

## 3.7 `actions/g1_actions.cpp`

该文件是动作库实现（被 `g1Arm7DIYActionExample` 链接使用），不是独立可执行程序。

---

## 4. 同一终端一次发送两个话题

串行发送：

```bash
ros2 topic pub --once /g1_arm/action_cmd std_msgs/msg/String "{data: '5'}" ; \
ros2 topic pub --once /gesture_cmd std_msgs/msg/String "{data: 'num3 right 800 2000'}"
```

并行发送：

```bash
ros2 topic pub --once /g1_arm/action_cmd std_msgs/msg/String "{data: '5'}" & \
ros2 topic pub --once /gesture_cmd std_msgs/msg/String "{data: 'num3 right 800 2000'}" & wait
```

---

## 5. 常见问题

1) `ros2 topic pub` 一直提示 `Waiting for at least 1 matching subscription(s)...`
- 检查对应节点是否已启动
- 检查话题名是否完全一致（例如 `/g1_arm/action_cmd`）

2) 动作不执行
- 先确认 `g1Arm7DIYActionExample` 已启动
- 再用 `ros2 topic info /g1_arm/action_cmd` 确认存在订阅者

3) 网络接口问题
- 运行参数网卡名必须与实际一致（如 `eno1`）

---

## 4. Docker 高层 + 宿主机 Humble 真机节点联调启动

当前推荐方案：高层仍在 Docker 内运行，但真机导航和手臂节点在宿主机 Humble 环境运行。

原因：Docker 内是 ROS2 Foxy，宿主机是真机 Humble。直接用 Docker/Foxy 访问宿主机 Humble 的 ROS2 action 不稳定，且 Docker/Foxy 中出现过 `bad_alloc caught: std::bad_alloc`。因此使用宿主机 Humble HTTP bridge 占用 `28180`，高层仍访问原来的 `localhost:28180`，但实际进入宿主机 Humble，再由 bridge 调用 `/navi_arm` 和 `/navi_way_point` action。

### 4.1 终端 1：启动手臂 action server

```bash
cd /mnt/ssd/navgation/projects/unitree_slam_example_new/example
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

./build/g1Arm7DIYActionExample eno1
```

看到下面内容表示手臂 action server 就绪：

```text
Waiting navi_arm action goals...
```

### 4.2 终端 2：启动导航 action server

如果要真机导航并自动重定位：

```bash
cd /mnt/ssd/navgation/projects/unitree_slam_example_new/example
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

./build/goGoalNavigation eno1 /home/unitree/test3.pcd --nav_speed=0.6
```

如果只是测试高层目标是否能被接收，可先不带 PCD：

```bash
cd /mnt/ssd/navgation/projects/unitree_slam_example_new/example
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

./build/goGoalNavigation eno1 --nav_speed=0.6
```

不带 PCD 或未完成重定位时，导航目标可以被接收，但会提示：

```text
[ActionGoal] No valid pose. Please start relocation first.
```

这表示通讯已到达导航节点，但当前没有有效定位，不能实际执行导航。

### 4.3 终端 3：启动宿主机 Humble 28180 bridge

必须在启动高层脚本前先启动这个 bridge，让它占用 `28180`。这样高层脚本会认为 Robot Agent 已运行，并跳过 Docker 内 Foxy 的 `robot_app.py`。

```bash
cd /mnt/ssd/navgation/projects
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

python3 -m uvicorn humble_robot_agent_bridge:app --host 0.0.0.0 --port 28180 --log-level info
```

健康检查：

```bash
curl -sS http://127.0.0.1:28180/health
```

预期输出：

```json
{"ok":true,"service":"humble_robot_agent_bridge"}
```

确认 `28180` 没有被 Docker Foxy `robot_app.py` 占用：

```bash
ss -ltnp | grep ':28180'
ps -eo pid,etime,rss,pcpu,args | grep -E 'humble_robot_agent_bridge|robot_app:app' | grep -v grep
```

预期应看到 `humble_robot_agent_bridge`，不要看到 `uvicorn robot_app:app --port 28180`。

### 4.4 检查 ROS2 action server

```bash
source /opt/ros/humble/setup.bash
source /mnt/ssd/navgation/projects/custom_action_ws/install/setup.bash

ros2 action info /navi_arm
ros2 action info /navi_way_point
```

预期：

```text
/navi_arm: Action servers: 1
/navi_way_point: Action servers: 1
```

### 4.5 Docker 内手动自测

手臂动作自测：

```bash
docker exec 989f62d3c100 bash -lc "curl -sS -X POST http://127.0.0.1:28180/do_arm_async --form-string 'task=right_hand_wave'"
```

预期输出：

```json
{"success":true,"message":"Action executed successfully"}
```

导航目标接收自测：

```bash
docker exec 989f62d3c100 bash -lc "curl -sS -X POST http://127.0.0.1:28180/go_to_async --form-string 'task=(1.3389, 0.3998, -0.1057, 0.0831, -0.7143, 0.6868)'"
```

预期输出：

```json
{"success":true,"message":"waypoint goal sent"}
```

如果导航节点未重定位，导航日志会提示 `No valid pose`，这是定位状态问题，不是通讯问题。

查询导航状态：

```bash
docker exec 989f62d3c100 bash -lc "curl -sS -X POST http://127.0.0.1:28180/go_to_status --form-string 'task='"
```

### 4.6 终端 4：启动 Docker 高层一键脚本

确认 `28180 bridge` 已经启动后，再启动高层：

```bash
cd /mnt/ssd/navgation/projects/rabbitbot-dev-ros2-master
bash scripts_1/start_all_services.sh
```

高层仍然使用：

```text
RABBITBOT_ROBOT_AGENT_URL=http://localhost:28180
```

无需修改高层端口号。只要宿主机 Humble bridge 先占用 `28180`，高层发布的手臂命令会进入 `/navi_arm`，导航命令会进入 `/navi_way_point`。

### 4.7 常用日志

如果使用后台启动，可参考以下日志路径：

```bash
tail -f /tmp/humble_robot_agent_bridge.log
tail -f /tmp/g1Arm7DIYActionExample_host.log
tail -f /tmp/goGoalNavigation_host_bridge.log
```

如果前台启动，则直接看对应终端输出。

### 4.8 注意事项

- 启动顺序很重要：先启动宿主机 `28180 bridge`，再启动 Docker 高层脚本。
- 如果 `28180` 已被 Docker 内 `robot_app.py` 占用，先停止它：

```bash
docker exec 989f62d3c100 bash -lc 'pkill -f "uvicorn robot_app:app" 2>/dev/null || true; pkill -f "scripts/start_robot_app.bash" 2>/dev/null || true'
```

- 手臂动作只要求 `/navi_arm` server 在线。
- 导航真机运动要求 `/navi_way_point` server 在线，并且 `goGoalNavigation` 已完成重定位、有有效 pose。


cd /mnt/ssd/navgation/projects/unitree_slam_example_new/example
./start_nav_arm_bridge.sh eno1 /home/unitree/test.pcd

ros2 topic pub --once /go_goal_pose std_msgs/msg/String "{data: '{\"x\":0.6491,\"y\":-5.4835,\"z\":0.1322,\"ox\":0.0042,\"oy\":-0.0166,\"oz\":-0.1295,
\"ow\":0.9914,\"mode\":1}'}"
