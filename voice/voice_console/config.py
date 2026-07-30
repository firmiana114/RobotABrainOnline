from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path("/home/unitree/RobotABrainOnline/voice")
DEFAULT_KOKORO_REPO_ID = "hexgrad/Kokoro-82M"
DEFAULT_KOKORO_VOICE = "zm_yunxi"


@dataclass(frozen=True)
class ConsoleConfig:
    project_root: Path
    host: str
    port: int
    dialogue_file: Path
    route_file: Path
    qa_context_file: Path
    runtime_status_file: Path
    interaction_log_file: Path
    navigation_project: Path
    map_root: Path
    navigation_service: str
    arm_service: str
    tour_service: str
    tour_control_url: str
    systemctl_path: Path
    sudo_path: Path | None
    pose_stale_seconds: float

    @classmethod
    def from_env(cls) -> "ConsoleConfig":
        root = Path(os.environ.get("VOICE_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))).resolve()
        sudo_value = os.environ.get("VOICE_CONSOLE_SUDO", "/usr/bin/sudo").strip()
        return cls(
            project_root=root,
            host=os.environ.get("VOICE_CONSOLE_HOST", "0.0.0.0"),
            port=int(os.environ.get("VOICE_CONSOLE_PORT", "8081")),
            dialogue_file=Path(os.environ.get("VOICE_DIALOGUE_FILE", str(root / "conf" / "dialogue_0.json"))),
            route_file=Path(os.environ.get("VOICE_ROUTE_FILE", str(root / "conf" / "dialogue_0_route.json"))),
            qa_context_file=Path(os.environ.get("VOICE_QA_CONTEXT_FILE", str(root / "guide_qa_context.txt"))),
            runtime_status_file=Path(os.environ.get("VOICE_RUNTIME_STATUS_FILE", str(root / "runtime" / "tour_status.json"))),
            interaction_log_file=Path(os.environ.get("VOICE_INTERACTION_LOG_FILE", str(root / "runtime" / "interactions.jsonl"))),
            navigation_project=Path(
                os.environ.get(
                    "VOICE_NAVIGATION_PROJECT",
                    str(root),
                )
            ),
            map_root=Path(os.environ.get("VOICE_MAP_ROOT", "/home/unitree")),
            navigation_service=os.environ.get("VOICE_NAVIGATION_SERVICE", "voice-navigation.service"),
            arm_service=os.environ.get("VOICE_ARM_SERVICE", "voice-arm-control.service"),
            tour_service=os.environ.get("VOICE_TOUR_SERVICE", "voice-tour-guide.service"),
            tour_control_url=os.environ.get("VOICE_TOUR_CONTROL_URL", "http://127.0.0.1:8766"),
            systemctl_path=Path(os.environ.get("VOICE_SYSTEMCTL_PATH", "/usr/bin/systemctl")),
            sudo_path=Path(sudo_value) if sudo_value else None,
            pose_stale_seconds=float(os.environ.get("VOICE_POSE_STALE_SECONDS", "5")),
        )
