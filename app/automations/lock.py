from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class LockBusy(Exception):
    pass


def _try_lock(file) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            file.seek(0)
            if file.tell() == 0 and file.read(1) == "":
                file.seek(0)
                file.write("0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(file) -> None:
    if os.name == "nt":
        import msvcrt

        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def file_lock(path: Path, timeout_seconds: float) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as file:
        path.chmod(0o600)
        deadline = time.monotonic() + timeout_seconds
        while not _try_lock(file):
            if time.monotonic() >= deadline:
                raise LockBusy
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            _unlock(file)
