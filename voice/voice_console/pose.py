from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request


logger = logging.getLogger(__name__)
POSE_FIELDS = ("x", "y", "z", "ox", "oy", "oz", "ow")


class PoseTracker:
    def __init__(self, stale_seconds: float = 5.0):
        self.stale_seconds = stale_seconds
        self._lock = threading.Lock()
        self._pose: dict | None = None
        self._updated_monotonic: float | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def update_from_text(self, raw: str) -> bool:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return False
            pose = {field: float(payload[field]) for field in POSE_FIELDS}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("忽略非法 /current_pose 消息：payload_length=%s", len(raw or ""))
            return False
        with self._lock:
            self._pose = pose
            self._updated_monotonic = time.monotonic()
        return True

    def snapshot(self) -> dict:
        with self._lock:
            pose = dict(self._pose) if self._pose else None
            updated = self._updated_monotonic
        age = time.monotonic() - updated if updated is not None else None
        fresh = pose is not None and age is not None and age <= self.stale_seconds
        result = {
            "available": pose is not None,
            "localized": fresh,
            "fresh": fresh,
            "age_seconds": round(age, 3) if age is not None else None,
            "message": "定位正常" if fresh else ("位姿已过期" if pose else "暂无定位位姿"),
        }
        if pose:
            result.update(pose)
        return result

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        bridge_url = os.environ.get("POSE_BRIDGE_URL", "").strip()
        if bridge_url:
            self._start_http_polling(bridge_url)
            return
        logger.error("POSE_BRIDGE_URL 未配置；位姿功能要求使用 Humble HTTP 桥")

    def _start_http_polling(self, bridge_url: str) -> None:
        self._stop.clear()

        def poll() -> None:
            logger.info("HTTP 位姿订阅已启动：url=%s stale_seconds=%s", bridge_url, self.stale_seconds)
            last_error_log = 0.0
            while not self._stop.wait(0.2):
                try:
                    with urllib.request.urlopen(bridge_url, timeout=1.0) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    pose = payload.get("pose")
                    if payload.get("available") and isinstance(pose, dict):
                        self.update_from_text(json.dumps(pose))
                except (OSError, UnicodeDecodeError, urllib.error.URLError, json.JSONDecodeError) as exc:
                    now = time.monotonic()
                    if now - last_error_log >= 30.0:
                        logger.warning("HTTP 位姿读取失败：error_type=%s", type(exc).__name__)
                        last_error_log = now

        self._thread = threading.Thread(target=poll, name="voice-console-pose-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
