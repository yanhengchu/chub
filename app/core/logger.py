from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.core.config import PROJECT_ROOT, LogsConfig


LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(config: LogsConfig) -> None:
    config.file.parent.mkdir(parents=True, exist_ok=True)
    config.operations_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(config.level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        config.file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    operation_logger = logging.getLogger("hub.operations")
    for handler in operation_logger.handlers:
        handler.close()
    operation_logger.handlers.clear()
    operation_logger.setLevel(logging.INFO)
    operation_logger.propagate = False
    operation_handler = RotatingFileHandler(
        config.operations_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    operation_handler.setFormatter(formatter)
    operation_logger.addHandler(operation_handler)

    for path in (
        config.file,
        config.operations_file,
        PROJECT_ROOT / "logs" / "service.out.log",
        PROJECT_ROOT / "logs" / "service.err.log",
    ):
        if path.exists():
            path.chmod(0o600)
