from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Callable
import urllib.error
import urllib.request

from .config import ConsoleConfig


logger = logging.getLogger(__name__)


class CommandError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


SERVICE_STATE_MAP = {
    "active": "active",
    "activating": "activating",
    "deactivating": "activating",
    "failed": "failed",
    "inactive": "inactive",
    "unknown": "inactive",
}


class SystemdManager:
    def __init__(
        self,
        config: ConsoleConfig,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.config = config
        self.runner = runner

    def _run(self, args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
        try:
            return self.runner(args, check=False, text=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            logger.error("系统命令超时：command=%s timeout=%s", args[0], timeout)
            raise CommandError("服务操作超时，请检查系统日志", 504) from exc
        except OSError as exc:
            logger.error("系统命令执行失败：command=%s error_type=%s", args[0], type(exc).__name__)
            raise CommandError(f"无法执行系统命令：{args[0]}", 500) from exc

    def state(self, service: str) -> str:
        result = self._run([str(self.config.systemctl_path), "is-active", service], timeout=5)
        raw = (result.stdout or result.stderr or "inactive").strip().splitlines()
        value = raw[-1] if raw else "inactive"
        return SERVICE_STATE_MAP.get(value, "inactive")

    def _external_process_pids(self, pattern: str) -> list[int]:
        result = self._run(["/usr/bin/pgrep", "-f", pattern], timeout=5)
        if result.returncode != 0:
            return []
        pids = []
        for raw_pid in (result.stdout or "").splitlines():
            try:
                pid = int(raw_pid.strip())
            except ValueError:
                continue
            if pid > 1 and pid != os.getpid():
                pids.append(pid)
        return sorted(set(pids))

    def _external_process_exists(self, pattern: str) -> bool:
        return bool(self._external_process_pids(pattern))

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def _terminate_external_processes(self, pattern: str, label: str) -> None:
        pids = self._external_process_pids(pattern)
        if not pids:
            return
        logger.info("准备终止外部进程并转交 systemd：label=%s process_count=%s pids=%s", label, len(pids), pids)
        try:
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    logger.error("终止外部进程失败：label=%s pid=%s error_type=%s", label, pid, type(exc).__name__)
                    raise CommandError(f"无法终止外部{label}进程 PID {pid}", 500) from exc
            deadline = time.monotonic() + 8
            remaining = [pid for pid in pids if self._pid_alive(pid)]
            while remaining and time.monotonic() < deadline:
                time.sleep(0.1)
                remaining = [pid for pid in remaining if self._pid_alive(pid)]
            if remaining:
                logger.warning("外部进程未响应 SIGTERM，执行精确 SIGKILL：label=%s pids=%s", label, remaining)
                for pid in remaining:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(0.1)
                remaining = [pid for pid in remaining if self._pid_alive(pid)]
            if remaining:
                raise CommandError(f"外部{label}进程未能退出：{remaining}", 500)
        except CommandError:
            raise
        except Exception as exc:
            logger.exception("清理外部进程异常：label=%s process_count=%s", label, len(pids))
            raise CommandError(f"清理外部{label}进程失败", 500) from exc
        logger.info("外部进程已退出，可由 systemd 接管：label=%s process_count=%s", label, len(pids))

    def _privileged_systemctl(self, action: str, service: str) -> str:
        args: list[str] = []
        if self.config.sudo_path:
            args.extend([str(self.config.sudo_path), "-n"])
        args.extend([str(self.config.systemctl_path), action, service])
        result = self._run(args, timeout=30)
        output = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            logger.error(
                "systemd 操作失败：action=%s service=%s returncode=%s output=%s",
                action,
                service,
                result.returncode,
                output[:500],
            )
            raise CommandError(output or f"{action} {service} 失败", 500)
        logger.info("systemd 操作完成：action=%s service=%s", action, service)
        return output

    def _start(self, service: str, process_pattern: str, label: str) -> dict:
        current = self.state(service)
        if current in {"active", "activating"}:
            return {"ok": True, "state": current, "message": f"{label}已启动"}
        if self._external_process_exists(process_pattern):
            logger.warning("检测到外部管理进程，拒绝重复启动：label=%s pattern=%s", label, process_pattern)
            raise CommandError(f"检测到非控制台管理的{label}进程，请先人工确认并清理", 409)
        if current == "failed":
            self._privileged_systemctl("reset-failed", service)
        self._privileged_systemctl("start", service)
        new_state = self.state(service)
        if new_state == "failed":
            raise CommandError(f"{label}启动失败，请查看 journalctl -u {service}", 500)
        return {"ok": True, "state": new_state, "message": f"{label}启动命令已提交"}

    def _restart(self, service: str, process_pattern: str, label: str) -> dict:
        current = self.state(service)
        if current == "inactive":
            self._terminate_external_processes(process_pattern, label)
        elif current == "failed":
            self._privileged_systemctl("reset-failed", service)
        self._privileged_systemctl("restart", service)
        new_state = self.state(service)
        if new_state == "failed":
            raise CommandError(f"{label}重启失败，请查看 journalctl -u {service}", 500)
        return {"ok": True, "state": new_state, "message": f"{label}重启命令已提交"}

    def start_navigation(self) -> dict:
        return self._restart(self.config.navigation_service, "[g]oGoalNavigation66", "导航")

    def start_arm(self) -> dict:
        return self._restart(self.config.arm_service, "[g]1ArmOfficialActionServer", "手臂控制")

    def _tour_prerequisite_ready(self, service: str, process_pattern: str, label: str) -> bool:
        state = self.state(service)
        if state == "active":
            return True
        external = state == "inactive" and self._external_process_exists(process_pattern)
        if external:
            logger.info("外部进程满足导览前置条件：label=%s service=%s", label, service)
        return external

    def _missing_tour_prerequisites(self) -> list[str]:
        missing = []
        if not self._tour_prerequisite_ready(
            self.config.navigation_service, "[g]oGoalNavigation66", "导航"
        ):
            missing.append("导航")
        if not self._tour_prerequisite_ready(
            self.config.arm_service, "[g]1ArmOfficialActionServer", "手臂控制"
        ):
            missing.append("手臂控制")
        return missing

    def _tour_control_request(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.config.tour_control_url.rstrip('/')}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=3.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = str(payload.get("message") or "导览控制请求失败")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                message = "导览控制请求失败"
            logger.warning("导览控制拒绝请求：path=%s status=%s", path, exc.code)
            raise CommandError(message, exc.code) from exc
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            logger.error("导览控制接口不可用：path=%s error_type=%s", path, type(exc).__name__)
            raise CommandError("导览服务尚未预热完成，请先重启导览服务并等待就绪", 503) from exc
        if not isinstance(payload, dict):
            raise CommandError("导览控制接口返回格式错误", 500)
        logger.info("导览控制请求完成：path=%s state=%s", path, payload.get("state"))
        return payload

    def restart_tour_service(self) -> dict:
        missing = self._missing_tour_prerequisites()
        if missing:
            raise CommandError(f"请先启动：{'、'.join(missing)}", 409)
        result = self._restart(
            self.config.tour_service,
            "[v]oice_console.supervisor",
            "导览服务",
        )
        result["message"] = "导览服务已重启，正在完成模型和语音预热"
        return result

    def start_tour(self) -> dict:
        missing = self._missing_tour_prerequisites()
        if missing:
            raise CommandError(f"请先启动：{'、'.join(missing)}", 409)
        if self.state(self.config.tour_service) != "active":
            raise CommandError("请先点击“重启导览服务”并等待预热完成", 409)
        return self._tour_control_request("/start")

    def stop_tour(self) -> dict:
        current = self.state(self.config.tour_service)
        if current != "active":
            return {"ok": True, "state": current, "message": "导览任务已经关闭"}
        result = self._tour_control_request("/stop")
        result["message"] = "导览任务停止请求已提交，预热服务保持运行"
        return result

    def all_states(self) -> dict[str, str]:
        return {
            "navigation": self.state(self.config.navigation_service),
            "arm": self.state(self.config.arm_service),
            "tour": self.state(self.config.tour_service),
        }

    def external_processes(self) -> dict[str, bool]:
        states = self.all_states()
        return {
            "navigation": states["navigation"] == "inactive" and self._external_process_exists("[g]oGoalNavigation66"),
            "arm": states["arm"] == "inactive" and self._external_process_exists("[g]1ArmOfficialActionServer"),
            "tour": states["tour"] == "inactive" and self._external_process_exists("[v]oice_console.supervisor"),
        }


def read_tour_status(path: Path, tour_state: str) -> dict:
    default = {
        "active": False,
        "status": "preparing" if tour_state in {"active", "activating"} else "service_stopped",
        "current_site": None,
        "next_site": None,
        "current_index": 0,
        "total_points": 0,
        "speech_state": "未在监听" if tour_state == "inactive" else "启动中",
        "updated_at": None,
    }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(raw, dict):
        return default
    default.update({key: raw.get(key, value) for key, value in default.items()})
    default["active"] = default.get("status") in {"starting", "running", "stopping"}
    if tour_state == "failed":
        default["status"] = "failed"
        default["speech_state"] = "导览异常"
    elif tour_state == "inactive":
        default["active"] = False
        default["status"] = "service_stopped"
        default["speech_state"] = "未在监听"
    return default
