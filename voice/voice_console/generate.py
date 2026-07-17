from __future__ import annotations

import argparse
import json
import logging

from .config import ConsoleConfig
from .dialogue import DialogueStore


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 voice 导览路线和问答上下文")
    parser.add_argument("--print-map", action="store_true", help="只输出解析后的导航地图路径")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = ConsoleConfig.from_env()
    store = DialogueStore(config.dialogue_file, config.route_file, config.qa_context_file, config.map_root)
    if args.print_map:
        print(store.map_path())
        return
    result = store.sync_runtime_files(backup=False)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
