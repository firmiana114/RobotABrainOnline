"""Real voice tour with in-process simulated navigation; never contacts ROS."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

from .dialogue import build_route, build_qa_context, read_dialogue
from .post_wait_runner import wait_after_segment


class SimulatedRobot:
    def __init__(self, travel_seconds=1.0):
        self.travel_seconds = travel_seconds

    def wait_for_nav_subscriber(self, timeout, cancel_event=None):
        print("[SIMULATION] no robot subscriber required", flush=True)
        return not (cancel_event and cancel_event.is_set())

    def publish_goal(self, goal):
        print("[SIMULATION] navigation goal skipped; no network request", flush=True)

    def wait_arrived(self, timeout, cancel_event=None):
        import nav_route_then_speak as route
        if cancel_event.wait(self.travel_seconds):
            raise route.TourCancelled("simulation cancelled")
        return "simulated_arrived"

    def send_arm_action(self, action_name):
        print(f"[SIMULATION] arm action skipped: {action_name}", flush=True)

    def get_logger(self):
        return logging.getLogger("debug_no_robot")

    def destroy_node(self):
        pass


def select_points(points, start, count):
    if start < 1 or start > len(points) or count < 0:
        raise ValueError("start-point must be within route; point-count must be >= 0")
    return points[start - 1:] if count == 0 else points[start - 1:start - 1 + count]


def build_parser():
    parser = argparse.ArgumentParser(description="机器人离线导览调试；真实录音、外放和云端问答，不连接 ROS")
    parser.add_argument("--dialogue", type=Path)
    parser.add_argument("--start-point", type=int, default=1)
    parser.add_argument("--point-count", type=int, default=1, help="0 = 从起点运行到最后")
    parser.add_argument("--travel-seconds", type=float, default=1.0)
    parser.add_argument("--post-wait-limit", type=float, default=2.0, help="台词后等待上限（秒）；-1 保留正式等待")
    parser.add_argument("--mic", default=os.getenv("DEBUG_MIC_DEVICE", "plughw:CARD=MINI,DEV=0"))
    parser.add_argument("--aplay-device", help="明确输出设备，例如 plughw:CARD=XXX,DEV=0 或 pulse")
    parser.add_argument("--speaker-name", default="REDMI Speaker 2-4550")
    parser.add_argument("--check", action="store_true", help="检查配置和音频设备后退出；不启动导览")
    return parser


def check_audio(mic, output):
    # Short hardware open tests, with silence on output. No recorded audio is saved.
    subprocess.run(["arecord", "-q", "-D", mic, "-f", "S16_LE", "-r", "48000",
                    "-c", "2", "-d", "1", "-t", "raw", "/dev/null"], check=True, timeout=5)
    subprocess.run(["aplay", "-q", "-D", output, "-t", "raw", "-f", "S16_LE",
                    "-r", "24000", "-c", "1"], input=bytes(4800), check=True, timeout=5)


def main():
    import nav_route_then_speak as route
    options = build_parser().parse_args()
    if not math.isfinite(options.travel_seconds) or options.travel_seconds < 0:
        raise ValueError("travel-seconds must be finite and >= 0")
    if not math.isfinite(options.post_wait_limit) or (options.post_wait_limit < 0 and options.post_wait_limit != -1):
        raise ValueError("post-wait-limit must be -1 or >= 0")
    root = Path(__file__).resolve().parents[1]
    data = read_dialogue(options.dialogue or root / "conf/dialogue_0.json")
    points = select_points(build_route(data), options.start_point, options.point_count)
    if not (os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("请先在 voice/runtime/voice.env 配置 API Key")
    output = options.aplay_device or route.resolve_aplay_device(options)
    if not output:
        raise RuntimeError("未找到音响声卡。蓝牙音响需先连接并选为系统输出，再传 --aplay-device pulse；不会自动使用其他输出。")
    check_audio(options.mic, output)
    print(f"[debug] audio_open_ok mic={options.mic} output={output} points={len(points)}", flush=True)
    print("[SIMULATION] robot disabled; live microphone and cloud STT/Qwen enabled", flush=True)
    if options.check:
        return
    (root / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="debug-tour-", dir=root / "runtime") as folder:
        route_file = Path(folder) / "route.json"
        context_file = Path(folder) / "qa.txt"
        route_file.write_text(json.dumps(points, ensure_ascii=False))
        context_file.write_text(build_qa_context(data))
        argv = ["--route-file", str(route_file), "--voice-project", str(root),
                "--qa-context-file", str(context_file), "--alsa-input-device", options.mic,
                "--aplay-device", output, "--qwen-model", os.getenv("QWEN_MODEL", "qwen-plus"),
                "--kokoro-repo-id", os.getenv("KOKORO_REPO_ID", "hexgrad/Kokoro-82M"),
                "--kokoro-voice", os.getenv("KOKORO_VOICE", "zm_yunxi")]
        if os.getenv("ENABLE_SEARCH", "1") == "1":
            argv.append("--enable-search")
        args = route.build_parser().parse_args(argv)
        args.qa_context_text = route.load_qa_context(str(context_file), args.qa_context_max_chars)
        app, stt = route.init_voice(args)
        cancelled = threading.Event()
        qa_stop = threading.Event()
        app.route_cancel_event = cancelled
        app.route_task_active = True
        node = SimulatedRobot(options.travel_seconds)
        original = route.speak_segment

        def speak_with_wait(app, node, segment, index, stt_enabled, aliases, cancel_event=None):
            original(app, node, segment, index, stt_enabled, aliases, cancel_event=cancel_event)
            adjusted = dict(segment)
            if options.post_wait_limit >= 0 and "post_wait_seconds" in adjusted:
                adjusted["post_wait_seconds"] = min(float(adjusted["post_wait_seconds"]), options.post_wait_limit)
            wait_after_segment(adjusted, sleep=cancelled.wait, cancelled=cancelled.is_set)
            if cancelled.is_set():
                raise route.TourCancelled("debug tour stopped")

        def stop(signum, frame):
            cancelled.set()
            app.stop_guide_event.set()

        old_handlers = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        route.speak_segment = speak_with_wait
        try:
            route.precache_route_audio(args, app)
            stt.start()
            if cancelled.wait(0.2) or stt.proc.poll() is not None:
                raise RuntimeError("麦克风采集进程未持续运行")
            threading.Thread(target=route.background_question_loop, args=(app, qa_stop), daemon=True).start()
            print("[debug] 开始播报；说“小智”打断并提问，Ctrl+C 退出", flush=True)
            route.execute_route_once(args, app, node, True, cancelled)
        except route.TourCancelled:
            print("[debug] stopped", flush=True)
        finally:
            cancelled.set()
            qa_stop.set()
            stt.running = False
            if stt.thread is not None:
                stt.thread.join(timeout=1.0)
            stt.stop()
            app.stop()
            route.speak_segment = original
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"[debug] {exc}") from None
