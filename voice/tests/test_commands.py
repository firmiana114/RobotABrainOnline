import json
import os
import signal

import pytest

from voice_console.commands import CommandError, SystemdManager, read_tour_status


class PrerequisiteManager(SystemdManager):
    def __init__(self, config, states, external):
        super().__init__(config)
        self.states = states
        self.external = external
        self.started = []
        self.terminated = []
        self.control_requests = []

    def state(self, service):
        return self.states.get(service, "inactive")

    def _external_process_exists(self, pattern):
        if "oGoalNavigation66" in pattern:
            return self.external.get("navigation", False)
        if "1ArmOfficialActionServer" in pattern:
            return self.external.get("arm", False)
        return self.external.get("tour", False)

    def _terminate_external_processes(self, pattern, label):
        self.terminated.append((pattern, label))

    def _privileged_systemctl(self, action, service):
        self.started.append((action, service))
        if action in {"start", "restart"}:
            self.states[service] = "active"
        return ""

    def _tour_control_request(self, path):
        self.control_requests.append(path)
        state = "starting" if path == "/start" else "stopping"
        return {"ok": True, "state": state, "message": "accepted"}


def test_external_navigation_and_arm_satisfy_tour_prerequisites(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {config.navigation_service: "inactive", config.arm_service: "inactive", config.tour_service: "active"},
        {"navigation": True, "arm": True, "tour": False},
    )

    result = manager.start_tour()

    assert result["state"] == "starting"
    assert manager.control_requests == ["/start"]
    assert manager.started == []


def test_restart_tour_service_restarts_python_process(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {
            config.navigation_service: "active",
            config.arm_service: "active",
            config.tour_service: "active",
        },
        {},
    )

    result = manager.restart_tour_service()

    assert result["state"] == "active"
    assert "预热" in result["message"]
    assert manager.started == [("restart", config.tour_service)]
    assert manager.control_requests == []


def test_stop_tour_uses_control_api_without_stopping_service(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {config.tour_service: "active"},
        {},
    )

    result = manager.stop_tour()

    assert result["state"] == "stopping"
    assert manager.control_requests == ["/stop"]
    assert manager.started == []


def test_start_tour_requires_preheated_service(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {
            config.navigation_service: "active",
            config.arm_service: "active",
            config.tour_service: "inactive",
        },
        {},
    )

    with pytest.raises(CommandError, match="重启导览服务"):
        manager.start_tour()

    assert manager.control_requests == []


def test_restart_navigation_migrates_external_process_to_systemd(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {config.navigation_service: "inactive"},
        {"navigation": True},
    )

    result = manager.start_navigation()

    assert result["state"] == "active"
    assert result["message"] == "导航重启命令已提交"
    assert manager.terminated == [("[g]oGoalNavigation66", "导航")]
    assert manager.started == [("restart", config.navigation_service)]


def test_external_process_cleanup_targets_only_detected_pids(config_and_store, monkeypatch):
    config, _store = config_and_store
    manager = SystemdManager(config)
    alive = {101, 202}
    signals = []
    monkeypatch.setattr(manager, "_external_process_pids", lambda _pattern: sorted(alive))

    def fake_kill(pid, requested_signal):
        if requested_signal == 0:
            if pid not in alive:
                raise ProcessLookupError
            return
        signals.append((pid, requested_signal))
        alive.discard(pid)

    monkeypatch.setattr(os, "kill", fake_kill)

    manager._terminate_external_processes("[g]oGoalNavigation66", "导航")

    assert signals == [(101, signal.SIGTERM), (202, signal.SIGTERM)]


def test_missing_external_arm_still_blocks_tour(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {config.navigation_service: "inactive", config.arm_service: "inactive", config.tour_service: "inactive"},
        {"navigation": True, "arm": False, "tour": False},
    )

    with pytest.raises(CommandError, match="手臂控制"):
        manager.start_tour()

    assert manager.started == []


def test_preheated_service_is_not_reported_as_running_task(config_and_store):
    config, _store = config_and_store
    config.runtime_status_file.parent.mkdir(parents=True)
    config.runtime_status_file.write_text(
        json.dumps({"status": "ready", "speech_state": "已预热"}),
        encoding="utf-8",
    )

    status = read_tour_status(config.runtime_status_file, "active")

    assert status["status"] == "ready"
    assert status["active"] is False
    assert status["speech_state"] == "已预热"


def test_running_task_is_active_while_service_remains_resident(config_and_store):
    config, _store = config_and_store
    config.runtime_status_file.parent.mkdir(parents=True)
    config.runtime_status_file.write_text(
        json.dumps({"status": "running", "speech_state": "监听中"}),
        encoding="utf-8",
    )

    status = read_tour_status(config.runtime_status_file, "active")

    assert status["status"] == "running"
    assert status["active"] is True
