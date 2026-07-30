from pathlib import Path

import pytest

from voice_console.config import ConsoleConfig
from voice_console.dialogue import DialogueStore


@pytest.fixture
def config_and_store(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "conf" / "dialogue_0.json"
    dialogue = tmp_path / "conf" / "dialogue_0.json"
    dialogue.parent.mkdir(parents=True)
    dialogue.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    config = ConsoleConfig(
        project_root=tmp_path,
        host="127.0.0.1",
        port=8081,
        dialogue_file=dialogue,
        route_file=dialogue.parent / "dialogue_0_route.json",
        qa_context_file=tmp_path / "guide_qa_context.txt",
        runtime_status_file=tmp_path / "runtime" / "tour_status.json",
        interaction_log_file=tmp_path / "runtime" / "interactions.jsonl",
        navigation_project=tmp_path / "navigation",
        map_root=Path("/home/unitree"),
        navigation_service="voice-navigation.service",
        arm_service="voice-arm-control.service",
        tour_service="voice-tour-guide.service",
        tour_control_url="http://127.0.0.1:8766",
        systemctl_path=Path("/usr/bin/systemctl"),
        sudo_path=Path("/usr/bin/sudo"),
        pose_stale_seconds=5.0,
    )
    return config, DialogueStore(dialogue, config.route_file, config.qa_context_file, config.map_root)
