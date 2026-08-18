from __future__ import annotations

import logging
import os
import stat

import pytest

from app.core.logger import (
    configure_operation_logging,
    configure_worker_operation_logging,
)


@pytest.mark.parametrize(
    ("configure", "path_name"),
    [
        (configure_operation_logging, "operations_file"),
        (configure_worker_operation_logging, "worker_operations_file"),
    ],
)
def test_operation_log_rollover_keeps_current_file_private(
    settings,
    configure,
    path_name: str,
) -> None:
    previous_umask = os.umask(0o022)
    operation_logger = logging.getLogger("hub.operations")
    try:
        configure(settings.logs)
        handler = operation_logger.handlers[0]
        handler.maxBytes = 80
        operation_logger.info("first operation")
        operation_logger.info("x" * 100)
    finally:
        os.umask(previous_umask)
        for handler in operation_logger.handlers:
            handler.close()
        operation_logger.handlers.clear()

    path = getattr(settings.logs, path_name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.with_suffix(".log.1").stat().st_mode) == 0o600
