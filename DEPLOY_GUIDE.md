# 本项目 Orin 部署指南

适用于 **Ubuntu 22.04、Python 3.10 的 Jetson Orin**。语音和网页运行在宿主机，ROS 2 导航/手臂运行在 Docker。本文使用普通用户的 `systemctl --user`，不要运行旧 NX 的 `install_services.sh` 或 `install_nx_venv.sh`。

除特别说明外，命令均在 Orin 的 SSH 终端执行；拉取代码后留在项目根目录。先完成软件和语音验证，再接入机器人。

## 1. 准备系统与源码

需要：已安装 JetPack/CUDA、Docker Engine 和 Compose V2，能访问 Docker Hub、Ubuntu/ROS 软件源、GitHub、Hugging Face 和 DashScope。麦克风、音响和机器人网络在后续步骤接入。

```bash
uname -m                        # 应为 aarch64
python3 --version               # 应为 Python 3.10
cat /etc/nv_tegra_release        # 记录系统版本，用于匹配 torch
readlink -f /usr/local/cuda

docker ps                       # 当前用户不加 sudo 即可使用
docker compose version
```

若 Docker 未安装，先按 [Docker Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/)安装；仅有权限问题时执行 `sudo usermod -aG docker "$(id -un)"`，然后退出并重新登录。系统依赖：

```bash
sudo apt-get update
sudo apt-get install -y git curl python3-venv python3-dev python3-pip \
  build-essential libopenblas-dev portaudio19-dev libsndfile1 \
  alsa-utils libasound2-plugins pulseaudio-utils

mkdir -p /mnt/disk1/gt
cd /mnt/disk1/gt
git clone https://github.com/firmiana114/RobotABrainOnline.git
cd RobotABrainOnline
mkdir -p ../RobotABrainOnline-runtime/lib ../RobotABrainOnline-runtime/wheels
```

`voice/.venv`、`voice/hf_cache`、`voice/runtime` 不随 Git 提供，需要下面各步创建。

## 2. 从源码构建 Docker 镜像

**不需要原镜像 tar，也不需要访问 `ghcr.io/aaronai/rabbitbot-nav-portable:20260611`。** 本项目 Dockerfile 从官方 `ros:humble-ros-base-jammy` 安装依赖，再编译自定义 Action 和六个 C++ 节点。Unitree SDK2 使用官方仓库提供的头文件和预编译库，不要求自行编译 SDK 底层库。

```bash
git clone https://github.com/unitreerobotics/unitree_sdk2.git \
  ../RobotABrainOnline-runtime/unitree_sdk2
# 固定 SDK 版本，避免以后默认分支变化影响构建
git -C ../RobotABrainOnline-runtime/unitree_sdk2 checkout \
  d1abf001220d3e5f59e00a0c4aaa595e00089a4a

UNITREE_SDK2_CONTEXT="$PWD-runtime/unitree_sdk2" ./docker/build_image.sh
docker run --rm unitree_slam_example_new:humble check
```

最后应输出 `embedded runtime check passed nodes=6`。自检只检查文件和动态库，不启动机器人节点。网络受限时才需要另行提供离线镜像；源码构建仍需下载官方基础镜像和软件包。

## 3. 安装宿主 Python 与 CUDA 依赖

先按 [NVIDIA Jetson PyTorch 安装说明](https://docs.nvidia.com/deeplearning/frameworks/install-pytorch-jetson-platform/index.html)，取得**与本机 JetPack、Python 3.10、aarch64 匹配**的 torch wheel，并准备其要求的 cuSPARSELt 等运行库。不能用普通 PC 的 torch 包代替。不同 JetPack 的安装包不同，本项目没有通用 wheel。

```bash
python3.10 -m venv voice/.venv
voice/.venv/bin/python -m pip install --upgrade pip setuptools wheel
export LD_LIBRARY_PATH="$PWD-runtime/lib:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"

# 输入已经下载并核对兼容性的 wheel 路径
read -r -p 'Jetson torch wheel 路径: ' TORCH_WHEEL
voice/.venv/bin/python -m pip install "$TORCH_WHEEL"
voice/.venv/bin/python -m pip install \
  -r voice/requirements-nx-console.txt -r voice/requirements-nx-tour.txt
voice/.venv/bin/python -m pip install --no-deps kokoro==0.9.4 misaki==0.9.4
voice/.venv/bin/python -m pip install \
  'huggingface-hub<1' loguru 'transformers<4.47' addict regex \
  espeakng-loader num2words phonemizer-fork 'spacy<3.8' \
  'spacy-curated-transformers<0.3' cn2an jieba ordered-set pypinyin pypinyin-dict
voice/.venv/bin/python -m spacy download en_core_web_sm
voice/.venv/bin/python -m pip check
voice/.venv/bin/python - <<'PY'
import torch
print(torch.__version__, torch.cuda.is_available())
assert torch.cuda.is_available(), 'CUDA unavailable'
print(torch.ones(3, device='cuda').sum().item())
PY
```

应无依赖冲突、CUDA 为 `True`、运算结果为 `3.0`。文件名中的 `nx` 只是这组依赖清单的名称，不需要执行 NX 安装脚本。宿主环境不安装 ROS 或 `rclpy`。

若报 `libcusparseLt.so.0` 缺失或符号错误，提供匹配的真正 cuSPARSELt 库到 `../RobotABrainOnline-runtime/lib/`；不能把 `libcusparse.so` 链接或改名代替。服务和预热脚本会优先读取该目录。

## 4. 下载模型并验证语音合成

```bash
HF_HUB_OFFLINE=0 voice/.venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
for name in ('config.json', 'kokoro-v1_0.pth', 'voices/zm_yunxi.pt'):
    hf_hub_download('hexgrad/Kokoro-82M', name, cache_dir='voice/hf_cache/hub')
PY
voice/scripts/prewarm_offline.sh
```

预热成功时最后输出 JSON，`cuda` 为 `true`、`segments` 大于 0。此步无需 API Key，不录音、不播放、不调用机器人。后续运行从缓存离线加载模型，但语音识别和问答仍需联网。

## 5. 配置密钥、音频与用户服务

```bash
python3 voice/scripts/install_user_services.py   # 生成配置与服务，不启动
arecord -l                                     # 查看麦克风
pactl list short sinks                         # 查看系统音频输出
```

编辑 `voice/runtime/voice.env`，填入或调整：

```ini
DASHSCOPE_API_KEY=你的有效密钥
QWEN_MODEL=qwen-plus
KOKORO_REPO_ID=hexgrad/Kokoro-82M
KOKORO_VOICE=zm_yunxi
ALSA_INPUT_DEVICE=plughw:CARD=MINI,DEV=0
```

麦克风值必须按实际设备替换。播放默认通过 `pulse` 使用系统默认音响；在系统声音设置中选中音响，或执行 `pactl set-default-sink 实际输出名称`。不要依靠 `SPEAKER_NAME` 切换音响。

```bash
chmod 600 voice/runtime/voice.env
sudo loginctl enable-linger "$(id -un)"
```

密钥文件不要提交或贴到日志中。暂时保持 `voice/runtime/deployment.env` 的 `ROS_INTERFACE=lo`，只使用本机回环网络。

## 6. 启动控制台并验证真实语音

先确认 `8081` 未被其他程序占用，Docker 已运行：

```bash
ss -ltnp | grep ':8081'            # 有输出时先核对占用者
systemctl is-active docker
systemctl --user enable --now voice-control-console voice-arm-bridge
curl -fsS http://127.0.0.1:8765/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/
```

桥应返回 `ok: true`，网页返回 `200`；电脑浏览器打开 `http://Orin实际IP:8081`。暂时不要点导航、手臂或导览按钮。

接好麦克风和音响，运行无机器人语音调试（默认 DJI MIC MINI，其他设备加 `--mic 实际ALSA设备名`）：

```bash
voice/scripts/debug_tour_no_robot.sh --check
voice/scripts/debug_tour_no_robot.sh
```

`--check` 会短暂录音并播放静音。正式调试应能听见播报，说“小智，……”可提问；确认听到回答后按 `Ctrl+C` 退出。不要与正式导览服务同时占用麦克风。到此完成软件与语音验收；导航、手臂和正式导览服务应保持停止且不设自启。

## 7. 接入机器人并启动导览

先确认地图、路线点位、机器人活动范围和急停。地图路径由机器人 PC1 的 SLAM 服务解析；示例台词文件里的坐标不能直接当作新现场路线使用。

用 `ip -brief addr` 确认机器人网卡已连接且配置了正确 IP。在 `voice/runtime/deployment.env` 中把两个值都改为实际网卡，例如：

```ini
ROS_INTERFACE=eno1
NAV_IFACE=eno1
```

这个文件不会设置网卡 IP。确认 `voice/conf/dialogue_0.json`（或控制台选中的台词文件）适用于现场，再逐项启动；**导航启动会进行初始化/重定位，以下操作可能触发真实动作**。

```bash
systemctl --user restart voice-arm-bridge
curl -fsS http://127.0.0.1:8765/healthz
systemctl --user start voice-navigation
systemctl --user status voice-navigation --no-pager
systemctl --user start voice-arm-control
systemctl --user status voice-arm-control --no-pager
systemctl --user start voice-tour-guide
curl -s http://127.0.0.1:8766/status
```

每一步成功再进行下一步。最后等待状态 `ready`，通过网页点击“启动导览”。`active` 只表示进程存活；还要确认定位和真实语音正常。当前版本正式预热需要导航订阅者，不能在离线部署阶段直接启动正式导览服务。

停止当前任务、保留预热进程：

```bash
curl -sS -X POST http://127.0.0.1:8766/stop
```

结束使用后依次停止服务；停止导览本身不会自动停止导航和手臂：

```bash
systemctl --user stop voice-tour-guide
systemctl --user stop voice-navigation
systemctl --user stop voice-arm-control
systemctl --user stop voice-arm-bridge
```

## 常见故障

| 现象 | 检查 |
| --- | --- |
| Docker 构建失败 | 查看首个错误：Docker Hub/ROS 软件源是否可达，SDK 目录是否完整；不要再拉取旧 GHCR 地址。 |
| CUDA 导入/运算失败 | 核对 JetPack 对应的 torch 和 cuSPARSELt；不能只看包安装成功。 |
| 模型找不到 | 缓存须在 `voice/hf_cache/hub`，重新执行步骤 4。 |
| 服务找不到或无法连接用户总线 | 用部署用户正常 SSH 登录，使用 `systemctl --user`，不要加 sudo。 |
| 控制台端口冲突 | 核对 8081 占用者后调整对应服务，不要随意杀进程。 |
| 听不见声音 | `pactl info`、默认输出、音量和静音状态；`--check` 成功不等于现场听感正常。 |
| 识别/问答失败 | 检查网络、DNS、Key 和日志；正式服务可能降级为仅播报。 |
| `no subscriber on /go_goal_pose` | 先启动导航，核对两个网卡变量与桥日志。 |

日志示例：`journalctl --user -u voice-tour-guide -n 80 --no-pager`，替换服务名即可查看其他组件。改台词、模型或音色后，重启导览服务重新预热。架构与开发说明见 [PROJECT_GUIDE.md](PROJECT_GUIDE.md)。
