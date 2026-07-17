import logging

from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE
from voice_console.supervisor import TourSupervisor


def test_tour_command_uses_yunxi_voice_by_default(config_and_store, monkeypatch):
    config, _store = config_and_store
    config.navigation_project.mkdir(parents=True)
    (config.navigation_project / "nav_route_then_speak.py").write_text("", encoding="utf-8")
    monkeypatch.delenv("KOKORO_VOICE", raising=False)
    monkeypatch.delenv("KOKORO_REPO_ID", raising=False)

    command = TourSupervisor(config)._command()

    voice_index = command.index("--kokoro-voice")
    repo_index = command.index("--kokoro-repo-id")
    assert DEFAULT_KOKORO_REPO_ID == "hexgrad/Kokoro-82M"
    assert DEFAULT_KOKORO_VOICE == "zm_yunxi"
    assert command[repo_index + 1] == "hexgrad/Kokoro-82M"
    assert command[voice_index + 1] == "zm_yunxi"


def test_tour_command_allows_voice_environment_override(config_and_store, monkeypatch):
    config, _store = config_and_store
    config.navigation_project.mkdir(parents=True)
    (config.navigation_project / "nav_route_then_speak.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("KOKORO_VOICE", "zm_080")
    monkeypatch.setenv("KOKORO_REPO_ID", "example/custom-kokoro")

    command = TourSupervisor(config)._command()

    voice_index = command.index("--kokoro-voice")
    repo_index = command.index("--kokoro-repo-id")
    assert command[voice_index + 1] == "zm_080"
    assert command[repo_index + 1] == "example/custom-kokoro"


def test_supervisor_logs_safe_startup_metrics(config_and_store, caplog):
    config, _store = config_and_store
    supervisor = TourSupervisor(config)

    with caplog.at_level(logging.INFO):
        supervisor._handle_line(
            "[startup] voice_ready elapsed_seconds=18.001 "
            "total_seconds=18.120 repo=hexgrad/Kokoro-82M voice=zm_yunxi"
        )

    assert "stage=voice_ready" in caplog.text
    assert "elapsed_seconds=18.001" in caplog.text
