import logging

import uvicorn

from .config import ConsoleConfig


def main() -> None:
    config = ConsoleConfig.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        "voice_console.app:app",
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
