from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import threading
from typing import Callable


logger = logging.getLogger(__name__)


class TourControlError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class TourTaskController:
    STARTABLE_STATES = {"ready", "completed", "stopped", "failed"}
    ACTIVE_STATES = {"starting", "running", "stopping"}

    def __init__(self, stop_playback: Callable[[], None] | None = None):
        self._lock = threading.Lock()
        self._state = "preparing"
        self._stop_playback = stop_playback
        self.start_event = threading.Event()
        self.cancel_event = threading.Event()
        self.shutdown_event = threading.Event()

    def snapshot(self) -> dict:
        with self._lock:
            state = self._state
        return {
            "ok": True,
            "state": state,
            "ready": state in self.STARTABLE_STATES,
            "running": state in self.ACTIVE_STATES,
        }

    def mark_ready(self) -> None:
        with self._lock:
            if not self.shutdown_event.is_set():
                self._state = "ready"

    def request_start(self) -> dict:
        with self._lock:
            if self.shutdown_event.is_set():
                raise TourControlError("导览服务正在关闭", 503)
            if self._state not in self.STARTABLE_STATES:
                if self._state == "preparing":
                    raise TourControlError("导览服务仍在预热，请稍后重试", 409)
                raise TourControlError("导览任务已经启动或正在停止", 409)
            self._state = "starting"
            self.cancel_event.clear()
            self.start_event.set()
        logger.info("收到导览任务启动请求")
        print("[control] tour_starting", flush=True)
        return {"ok": True, "state": "starting", "message": "导览任务已立即启动"}

    def wait_for_start(self, timeout: float = 0.5) -> bool:
        requested = self.start_event.wait(timeout)
        if requested:
            self.start_event.clear()
        return requested

    def mark_running(self) -> None:
        with self._lock:
            if not self.shutdown_event.is_set():
                self._state = "running"

    def request_stop(self) -> dict:
        should_stop_playback = False
        with self._lock:
            if self._state in {"starting", "running"}:
                self._state = "stopping"
                self.cancel_event.set()
                should_stop_playback = True
            elif self._state == "stopping":
                return {"ok": True, "state": "stopping", "message": "导览任务正在关闭"}
            else:
                return {"ok": True, "state": self._state, "message": "导览任务已经关闭"}
        if should_stop_playback and self._stop_playback is not None:
            try:
                self._stop_playback()
            except Exception as exc:
                logger.exception("停止当前导览播放失败：error_type=%s", type(exc).__name__)
        logger.info("收到导览任务停止请求")
        print("[control] tour_stopping", flush=True)
        return {"ok": True, "state": "stopping", "message": "导览任务停止请求已提交"}

    def mark_finished(self, state: str) -> None:
        if state not in {"completed", "stopped", "failed"}:
            raise ValueError(f"invalid finished state: {state}")
        with self._lock:
            if not self.shutdown_event.is_set():
                self._state = state
        self.cancel_event.clear()

    def request_shutdown(self) -> None:
        self.shutdown_event.set()
        self.cancel_event.set()
        self.start_event.set()
        if self._stop_playback is not None:
            try:
                self._stop_playback()
            except Exception as exc:
                logger.exception("关闭导览服务时停止播放失败：error_type=%s", type(exc).__name__)


class TourControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: TourTaskController):
        self.controller = controller
        super().__init__(address, TourControlHandler)


class TourControlHandler(BaseHTTPRequestHandler):
    server: TourControlServer

    def _write_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/status":
            self._write_json(404, {"ok": False, "message": "not found"})
            return
        self._write_json(200, self.server.controller.snapshot())

    def do_POST(self) -> None:
        try:
            if self.path == "/start":
                payload = self.server.controller.request_start()
            elif self.path == "/stop":
                payload = self.server.controller.request_stop()
            else:
                self._write_json(404, {"ok": False, "message": "not found"})
                return
        except TourControlError as exc:
            self._write_json(
                exc.status_code,
                {"ok": False, "state": self.server.controller.snapshot()["state"], "message": str(exc)},
            )
            return
        self._write_json(200, payload)

    def log_message(self, _format: str, *_args) -> None:
        return
