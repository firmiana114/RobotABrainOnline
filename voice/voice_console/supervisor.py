from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

from .config import ConsoleConfig
from .dialogue import DialogueStore


logger = logging.getLogger(__name__)
ROUTE_LINE = re.compile(r"^\[route\]\s+(\d+)/(\d+)\s+->\s+(.+)$")
POST_WAIT_LINE = re.compile(r"^\[route\] post-wait (start|complete) seconds=([0-9.]+)$")
SAFE_NAV_LINE = re.compile(r"\[(?:nav_status|goal|arm)\]")
SENSITIVE_MARKERS = ("用户问题", "识别", "Qwen", "导览:", "等待你说完", "正在听你说话")


class TourSupervisor:
    def __init__(self, config: ConsoleConfig):
        self.config = config
        self.store = DialogueStore(config.dialogue_file, config.route_file, config.qa_context_file, config.map_root)
        self.process: subprocess.Popen | None = None
        self.stop_event = threading.Event()
        self.status = {
            "active": True,
            "status": "starting",
            "current_site": None,
            "next_site": None,
            "current_index": 0,
            "total_points": 0,
            "speech_state": "启动中",
            "updated_at": self._now(),
        }
        self.route_names: list[str] = []

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    def _write_status(self, **updates) -> None:
        self.status.update(updates)
        self.status["updated_at"] = self._now()
        path = self.config.runtime_status_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.status, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def _command(self) -> list[str]:
        navigation_script = self.config.navigation_project / "nav_route_then_speak.py"
        if not navigation_script.is_file():
            raise RuntimeError(f"路线脚本不存在：{navigation_script}")
        command = [
            sys.executable,
            "-m",
            "voice_console.post_wait_runner",
            "--navigation-script",
            str(navigation_script),
            "--route-file",
            str(self.config.route_file),
            "--voice-project",
            str(self.config.project_root),
            "--alsa-input-device",
            os.environ.get("ALSA_INPUT_DEVICE", "plughw:CARD=Rx,DEV=0"),
            "--alsa-input-channels",
            os.environ.get("ALSA_INPUT_CHANNELS", "2"),
            "--input-sample-rate",
            os.environ.get("INPUT_SAMPLE_RATE", "48000"),
            "--stt-sample-rate",
            os.environ.get("STT_SAMPLE_RATE", "16000"),
            "--qwen-model",
            os.environ.get("QWEN_MODEL", "qwen-plus"),
            "--speaker-name",
            os.environ.get("SPEAKER_NAME", "REDMI Speaker 2-6002"),
            "--qa-context-file",
            str(self.config.qa_context_file),
        ]
        if os.environ.get("ENABLE_SEARCH", "1") == "1":
            command.append("--enable-search")
        return command

    def _load_route_names(self) -> None:
        route = json.loads(self.config.route_file.read_text(encoding="utf-8"))
        self.route_names = [str(item.get("scene") or item.get("name") or "未知点位") for item in route]
        self._write_status(total_points=len(self.route_names), next_site=self.route_names[0] if self.route_names else None)

    def _handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        match = ROUTE_LINE.match(line)
        if match:
            index = int(match.group(1))
            total = int(match.group(2))
            site = match.group(3).strip()
            next_site = self.route_names[index] if index < len(self.route_names) else None
            self._write_status(
                status="running",
                current_index=index,
                total_points=total,
                current_site=site,
                next_site=next_site,
                speech_state="监听中",
            )
            logger.info("导览进入点位：index=%s total=%s site=%s", index, total, site)
            return
        if "failed to start arecord STT" in line:
            self._write_status(speech_state="语音监听不可用")
            logger.warning("导览 STT 启动失败，已进入仅播报模式")
            return
        if "回答播放完成" in line:
            self._write_status(speech_state="监听中")
            logger.info("问答播放完成，恢复监听")
            return
        if "Qwen(stream)" in line or "用户问题" in line:
            self._write_status(speech_state="回答中")
            return
        if "[route] finished" in line:
            self._write_status(status="completed", next_site=None, speech_state="未在监听")
            logger.info("导览路线执行完成")
            return
        post_wait = POST_WAIT_LINE.match(line)
        if post_wait:
            logger.info("导览台词等待：phase=%s seconds=%s", post_wait.group(1), post_wait.group(2))
            return
        if SAFE_NAV_LINE.search(line):
            logger.info("导览子进程事件：event=ros line_length=%s", len(line))
            return
        if any(marker in line for marker in SENSITIVE_MARKERS):
            logger.info("导览子进程事件已脱敏：event=speech line_length=%s", len(line))
            return
        if "Traceback" in line or "Error" in line or "Exception" in line or "failed" in line.lower():
            logger.warning("导览子进程诊断：line=%s", line[:500])

    def stop(self, *_args) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self._write_status(status="stopping", speech_state="正在关闭")
        process = self.process
        if process is not None and process.poll() is None:
            logger.info("正在终止导览子进程：pid=%s", process.pid)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self) -> int:
        self.store.sync_runtime_files(backup=False)
        self._load_route_names()
        command = self._command()
        logger.info(
            "准备启动导览：route=%s qa_context=%s navigation_script=%s wait_adapter=%s",
            self.config.route_file,
            self.config.qa_context_file,
            self.config.navigation_project / "nav_route_then_speak.py",
            "voice_console.post_wait_runner",
        )
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._write_status(status="running", speech_state="监听中")
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._handle_line(line)
        try:
            return_code = self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            return_code = self.process.wait(timeout=5)
        final_status = "stopped" if self.stop_event.is_set() else ("completed" if return_code == 0 else "failed")
        self._write_status(active=False, status=final_status, speech_state="未在监听", exit_code=return_code)
        logger.info("导览子进程退出：returncode=%s status=%s", return_code, final_status)
        return return_code


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    supervisor = TourSupervisor(ConsoleConfig.from_env())
    signal.signal(signal.SIGTERM, supervisor.stop)
    signal.signal(signal.SIGINT, supervisor.stop)
    try:
        raise SystemExit(supervisor.run())
    except Exception:
        supervisor._write_status(active=False, status="failed", speech_state="导览异常")
        logger.exception("导览监督进程异常退出")
        raise


if __name__ == "__main__":
    main()
