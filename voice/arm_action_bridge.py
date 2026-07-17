#!/usr/bin/env python3
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
import threading
import time
from urllib.parse import urlparse

from custom_action_interfaces.action import NaviArm
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String


logger = logging.getLogger("arm_action_bridge")
VALID_ACTION = re.compile(r"^[A-Za-z0-9_]+$")
ARRIVED = {"arrived", "mid_arrived"}
FAILED = {"failed", "nav_failed", "error"}


class RosBridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("voice_humble_http_bridge")
        self.client = ActionClient(self, NaviArm, "/navi_arm")
        self.goal_pub = self.create_publisher(String, "/go_goal_pose", 10)
        self.create_subscription(String, "/nav_status", self._on_nav_status, 10)
        self.create_subscription(String, "/current_pose", self._on_pose, 10)
        self._state_lock = threading.Lock()
        self._nav_status = ""
        self._nav_status_seq = 0
        self._navigating_seq = 0
        self._arrived_seq = 0
        self._failed_seq = 0
        self._pose = None
        self._pose_updated = None

    def _on_nav_status(self, message: String) -> None:
        status = (message.data or "").strip()
        with self._state_lock:
            self._nav_status = status
            self._nav_status_seq += 1
            if status == "navigating":
                self._navigating_seq = self._nav_status_seq
            elif status in ARRIVED:
                self._arrived_seq = self._nav_status_seq
            elif status in FAILED:
                self._failed_seq = self._nav_status_seq
        if status:
            logger.info("navigation status status=%s", status)

    def _on_pose(self, message: String) -> None:
        try:
            pose = json.loads(message.data)
            if not isinstance(pose, dict):
                raise ValueError("pose is not an object")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("invalid pose message error_type=%s", type(exc).__name__)
            return
        with self._state_lock:
            self._pose = pose
            self._pose_updated = time.monotonic()

    def state(self) -> dict:
        with self._state_lock:
            return {
                "nav_status": self._nav_status,
                "status_seq": self._nav_status_seq,
                "navigating_seq": self._navigating_seq,
                "arrived_seq": self._arrived_seq,
                "failed_seq": self._failed_seq,
                "goal_subscribers": self.goal_pub.get_subscription_count(),
            }

    def pose(self) -> dict:
        with self._state_lock:
            pose = dict(self._pose) if self._pose else None
            updated = self._pose_updated
        return {
            "available": pose is not None,
            "age_seconds": round(time.monotonic() - updated, 3) if updated is not None else None,
            "pose": pose,
        }

    def publish_goal(self, goal: dict) -> int:
        message = String()
        message.data = json.dumps(goal, ensure_ascii=False)
        subscribers = self.goal_pub.get_subscription_count()
        self.goal_pub.publish(message)
        logger.info("navigation goal published subscribers=%s payload_length=%s", subscribers, len(message.data))
        return subscribers

    def send(self, action_name: str, timeout: float = 5.0) -> tuple[bool, str]:
        if not self.client.wait_for_server(timeout_sec=timeout):
            return False, "action server unavailable"
        goal = NaviArm.Goal()
        goal.action_name = action_name
        future = self.client.send_goal_async(goal)
        deadline = time.monotonic() + timeout
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not future.done():
            return False, "goal acceptance timeout"
        handle = future.result()
        if handle is None or not handle.accepted:
            return False, "goal rejected"
        return True, "accepted"


class BridgeServer(ThreadingHTTPServer):
    node: RosBridgeNode


class Handler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("http client=%s status=%s", self.client_address[0], args[1] if len(args) > 1 else "unknown")

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._reply(200, {"ok": True, "service": "voice-humble-bridge"})
            return
        if parsed.path == "/nav_state":
            self._reply(200, {"ok": True, **self.server.node.state()})
            return
        if parsed.path == "/pose":
            self._reply(200, {"ok": True, **self.server.node.pose()})
            return
        if parsed.path != "/healthz":
            self._reply(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/navi_arm", "/goal"):
            self._reply(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 65536:
            self._reply(400, {"ok": False, "error": "invalid content length"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("invalid bridge request json path=%s error_type=%s", parsed.path, type(exc).__name__)
            self._reply(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._reply(400, {"ok": False, "error": "payload must be an object"})
            return
        if parsed.path == "/goal":
            required = ("x", "y", "z", "ox", "oy", "oz", "ow", "mode")
            if any(key not in payload for key in required):
                self._reply(400, {"ok": False, "error": "incomplete goal"})
                return
            subscribers = self.server.node.publish_goal(payload)
            self._reply(202, {"ok": True, "goal_subscribers": subscribers})
            return
        action_name = str(payload.get("action_name", "")).strip()
        if not VALID_ACTION.fullmatch(action_name):
            logger.warning("invalid arm action name length=%s", len(action_name))
            self._reply(400, {"ok": False, "error": "invalid action_name"})
            return
        try:
            accepted, message = self.server.node.send(action_name)
        except Exception as exc:
            logger.exception("arm action bridge failure action=%s", action_name)
            self._reply(500, {"ok": False, "error": type(exc).__name__})
            return
        logger.info("arm action result action=%s accepted=%s", action_name, accepted)
        self._reply(202 if accepted else 503, {"ok": accepted, "action_name": action_name, "message": message})


def main() -> None:
    parser = argparse.ArgumentParser(description="Humble NaviArm HTTP bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    rclpy.init()
    node = RosBridgeNode()
    executor = MultiThreadedExecutor(num_threads=2)
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, name="ros-spin", daemon=True)
    spin_thread.start()
    server = BridgeServer((args.host, args.port), Handler)
    server.node = node
    logger.info("Humble ROS HTTP bridge listening host=%s port=%s", args.host, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
