from voice_console.config import DEFAULT_KOKORO_VOICE
from voice_console.supervisor import TourSupervisor


def test_tour_command_uses_mature_male_voice_by_default(config_and_store, monkeypatch):
    config, _store = config_and_store
    config.navigation_project.mkdir(parents=True)
    (config.navigation_project / "nav_route_then_speak.py").write_text("", encoding="utf-8")
    monkeypatch.delenv("KOKORO_VOICE", raising=False)

    command = TourSupervisor(config)._command()

    voice_index = command.index("--kokoro-voice")
    assert DEFAULT_KOKORO_VOICE == "zm_029"
    assert command[voice_index + 1] == "zm_029"


def test_tour_command_allows_voice_environment_override(config_and_store, monkeypatch):
    config, _store = config_and_store
    config.navigation_project.mkdir(parents=True)
    (config.navigation_project / "nav_route_then_speak.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("KOKORO_VOICE", "zm_080")

    command = TourSupervisor(config)._command()

    voice_index = command.index("--kokoro-voice")
    assert command[voice_index + 1] == "zm_080"
