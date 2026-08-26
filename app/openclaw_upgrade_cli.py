from __future__ import annotations

import logging
import signal

from app.services.openclaw import OpenClawManager


SYNC_TIMEOUT_SECONDS = 180


class OpenClawUpgradeTimeout(TimeoutError):
    pass


def _timeout_handler(_signum, _frame) -> None:
    raise OpenClawUpgradeTimeout("OpenClaw synchronization timed out")


def main() -> int:
    manager = OpenClawManager()
    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, SYNC_TIMEOUT_SECONDS)
    try:
        before = manager.status()
        if not before.installed:
            print("OpenClaw is not installed; component synchronization skipped")
            return 0
        result = manager.control("restart")
        if not result.ready:
            raise RuntimeError("OpenClaw Gateway did not become ready")
        print(result.message)
        return 0
    except Exception as exc:
        logging.getLogger("hub.openclaw").error(
            "OpenClaw upgrade synchronization failed: %s",
            str(exc)[:300],
        )
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
