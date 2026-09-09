import threading
from types import SimpleNamespace

import pytest

from voice_console.interrupt import play_audio_file
from voice_console.supervisor import TourSupervisor
from voice_console.debug_no_robot import build_parser, check_audio


@pytest.mark.parametrize("device,expected", [(None, "pulse"), ("pulse", "pulse"), ("plughw:3,0", "plughw:3,0")])
def test_playback_routes_default_to_system(tmp_path, device, expected):
    commands = []
    def spawn(command):
        commands.append(command)
        return SimpleNamespace(poll=lambda: 0)
    play_audio_file(tmp_path / "test.wav", device, False, threading.Event(), lambda: True, popen=spawn)
    assert commands[0][:4] == ["aplay", "-q", "-D", expected]


def test_route_does_not_enumerate_hardware(monkeypatch):
    import nav_route_then_speak as route
    def forbidden(*args, **kwargs):
        pytest.fail("speaker enumeration must not bypass system audio")
    monkeypatch.setattr(route.subprocess, "run", forbidden)
    assert route.resolve_aplay_device(SimpleNamespace(aplay_device=None, speaker_name="REDMI")) == "pulse"


def test_supervisor_system_default_and_override(config_and_store, monkeypatch):
    config, _ = config_and_store
    config.navigation_project.mkdir(parents=True)
    (config.navigation_project / "nav_route_then_speak.py").touch()
    monkeypatch.delenv("APLAY_DEVICE", raising=False)
    monkeypatch.setenv("SPEAKER_NAME", "old USB device")
    command = TourSupervisor(config)._command()
    assert command[command.index("--aplay-device") + 1] == "pulse"
    assert "--speaker-name" not in command
    monkeypatch.setenv("APLAY_DEVICE", "plughw:3,0")
    command = TourSupervisor(config)._command()
    assert command[command.index("--aplay-device") + 1] == "plughw:3,0"


def test_debug_uses_system_by_default(monkeypatch):
    monkeypatch.delenv("APLAY_DEVICE", raising=False)
    assert build_parser().parse_args([]).aplay_device == "pulse"


def test_debug_rejects_dummy_output(monkeypatch):
    import voice_console.debug_no_robot as debug
    monkeypatch.setattr(debug.subprocess, "check_output", lambda cmd, **kw:
                        "auto_null\n" if cmd[1] == "get-default-sink" else "0 auto_null module-null-sink.c s16le\n")
    with pytest.raises(RuntimeError, match="输出"):
        check_audio("unused", "pulse")
