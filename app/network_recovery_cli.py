from __future__ import annotations

import sys

from app.core.config import load_settings
from app.core.logger import configure_operation_logging
from app.services.network_recovery import NetworkRecoveryError, restart_network


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print("chub: network-restart does not accept arguments", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
        configure_operation_logging(settings.logs)
        result = restart_network(settings, source_ip="local-cli")
    except (NetworkRecoveryError, RuntimeError) as exc:
        print(f"chub: network restart failed: {exc}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
