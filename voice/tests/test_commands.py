import os
import signal

import pytest

from voice_console.commands import CommandError, SystemdManager


class PrerequisiteManager(SystemdManager):
    def __init__(self, config, states, external):
        super().__init__(config)
        self.states = states
        self.external = external
        self.started = []
        self.terminated = []

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


def test_external_navigation_and_arm_satisfy_tour_prerequisites(config_and_store):
    config, _store = config_and_store
    manager = PrerequisiteManager(
        config,
        {config.navigation_service: "inactive", config.arm_service: "inactive", config.tour_service: "inactive"},
        {"navigation": True, "arm": True, "tour": False},
    )

    result = manager.start_tour()

    assert result["state"] == "active"
    assert manager.started == [("start", config.tour_service)]


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
