from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import PROJECT_ROOT, LogsConfig


LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
SENSITIVE_HTTP_LOGGERS = ("httpx", "httpcore")


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return os.fdopen(
                descriptor,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
            )
        except Exception:
            os.close(descriptor)
            raise


def _configure_operation_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    operation_logger = logging.getLogger("hub.operations")
    for handler in operation_logger.handlers:
        handler.close()
    operation_logger.handlers.clear()
    operation_logger.setLevel(logging.INFO)
    operation_logger.propagate = False
    operation_handler = _PrivateRotatingFileHandler(
        path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    operation_handler.setFormatter(formatter)
    operation_logger.addHandler(operation_handler)
    path.chmod(0o600)


def configure_operation_logging(config: LogsConfig) -> None:
    _configure_operation_logging(config.operations_file)


def configure_worker_operation_logging(config: LogsConfig) -> None:
    _configure_operation_logging(config.worker_operations_file)


def configure_logging(config: LogsConfig) -> None:
    config.file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(config.level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = _PrivateRotatingFileHandler(
        config.file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    for logger_name in SENSITIVE_HTTP_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    configure_operation_logging(config)

    for path in (
        config.file,
        PROJECT_ROOT / "logs" / "service.out.log",
        PROJECT_ROOT / "logs" / "service.err.log",
    ):
        if path.exists():
            path.chmod(0o600)
