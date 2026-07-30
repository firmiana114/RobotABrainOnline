import json
import logging

from voice_console.config import DEFAULT_KOKORO_REPO_ID, DEFAULT_KOKORO_VOICE
from voice_console.supervisor import TourSupervisor


def test_supervisor_reactivates_saved_dialogue_selection(config_and_store):
    config, store = config_and_store
    alternate_file = config.dialogue_file.with_name("dialogue_1.json")
    alternate_data = store.load()
    alternate_data["opening"]["handshake_welcome"] = "监督进程备用台词。"
    alternate_file.write_text(
        json.dumps(alternate_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    TourSupervisor(config).catalog.activate("dialogue_1.json")
    activation = TourSupervisor(config).catalog.activate()

    assert activation["dialogue_file"] == "dialogue_1.json"
    route = json.loads(config.route_file.read_text(encoding="utf-8"))
    assert any(
        segment.get("text") == "监督进程备用台词。"
        for node in route
        for segment in node.get("segments", [])
    )


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
    assert "--standby-control" in command
    assert command[command.index("--control-host") + 1] == "127.0.0.1"
    assert command[command.index("--control-port") + 1] == "8766"


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


def test_supervisor_logs_safe_tts_chunk_metrics(config_and_store, caplog):
    config, _store = config_and_store
    supervisor = TourSupervisor(config)

    with caplog.at_level(logging.INFO):
        supervisor._handle_line(
            "[tts] synthesis_ready chars=185 chunks=4 max_phonemes=215 "
            "pause_seconds=0.680 samples=904080"
        )

    assert "event=synthesis_ready" in caplog.text
    assert "max_phonemes=215" in caplog.text


def test_supervisor_tracks_preheated_task_lifecycle(config_and_store, monkeypatch):
    config, _store = config_and_store
    supervisor = TourSupervisor(config)
    supervisor.route_names = ["opening", "point_1"]
    monkeypatch.setattr(supervisor, "_load_route_names", lambda: None)

    supervisor._handle_line("[control] ready host=127.0.0.1 port=8766")
    assert supervisor.status["status"] == "ready"
    assert supervisor.status["speech_state"] == "已预热"

    supervisor._handle_line("[control] tour_started")
    assert supervisor.status["status"] == "running"

    supervisor._handle_line("[control] tour_stopped")
    assert supervisor.status["status"] == "stopped"
    assert supervisor.status["speech_state"] == "已预热"
