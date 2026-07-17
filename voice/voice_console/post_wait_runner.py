from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import time
from types import ModuleType
from typing import Callable

from .dialogue import _normalize_post_wait_seconds


def wait_after_segment(segment: object, sleep: Callable[[float], None] = time.sleep) -> float:
    if not isinstance(segment, dict) or "post_wait_seconds" not in segment:
        return 0.0
    seconds = float(_normalize_post_wait_seconds(segment["post_wait_seconds"], "导览台词段"))
    if seconds <= 0:
        return 0.0
    print(f"[route] post-wait start seconds={seconds:g}", flush=True)
    sleep(seconds)
    print(f"[route] post-wait complete seconds={seconds:g}", flush=True)
    return seconds


def _load_navigation_module(script: Path) -> ModuleType:
    script = script.resolve()
    if not script.is_file():
        raise FileNotFoundError(f"路线脚本不存在：{script}")
    spec = importlib.util.spec_from_file_location("voice_external_nav_route_then_speak", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载路线脚本：{script}")
    sys.path.insert(0, str(script.parent))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--navigation-script", required=True)
    options, remaining = parser.parse_known_args()
    module = _load_navigation_module(Path(options.navigation_script))
    original_speak_segment = module.speak_segment

    def speak_segment_with_wait(app, node, segment, index, stt_enabled, action_aliases):
        original_speak_segment(app, node, segment, index, stt_enabled, action_aliases)
        wait_after_segment(segment)

    module.speak_segment = speak_segment_with_wait
    sys.argv = [str(options.navigation_script), *remaining]
    module.main()


if __name__ == "__main__":
    main()
