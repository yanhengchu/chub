"""Fixed development implementation for the Weixin refinement stage.

This file is the only development-stage source accepted by Chub.  It receives
no route, Session, filesystem or network authority; the coordinator supplies
the single bounded refinement action for the persisted request.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.response import ApiError


DEVELOPMENT_IMPLEMENTATION_ID = "weixin-orchestration-dev"
MAX_DEVELOPMENT_SOURCE_BYTES = 128 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_SOURCE_PATH = (
    PROJECT_ROOT / "orchestration-modules" / "weixin-refinement" / "weixin_refinement.py"
)


@dataclass(frozen=True)
class WeixinDevelopmentImplementation:
    implementation_id: str
    source_hash: str


class WeixinDevelopmentStage:
    """Read and verify the one repository-owned development implementation."""

    def __init__(self, source_path: Path | None = None) -> None:
        self.source_path = source_path or DEVELOPMENT_SOURCE_PATH

    def snapshot(self) -> WeixinDevelopmentImplementation:
        path = self.source_path
        try:
            if path.is_symlink() or not path.is_file():
                raise OSError("development source is unavailable")
            source = path.read_bytes()
        except OSError as exc:
            raise ApiError(
                503,
                "weixin_orchestration_development_unavailable",
                "微信开发编排实现当前不可用，本次任务未执行。",
            ) from exc
        if not source or len(source) > MAX_DEVELOPMENT_SOURCE_BYTES:
            raise ApiError(
                503,
                "weixin_orchestration_development_unavailable",
                "微信开发编排实现当前不可用，本次任务未执行。",
            )
        return WeixinDevelopmentImplementation(
            implementation_id=DEVELOPMENT_IMPLEMENTATION_ID,
            source_hash=hashlib.sha256(source).hexdigest(),
        )

    def execute_refinement(
        self,
        *,
        implementation_id: str | None,
        source_hash: str | None,
        enqueue_refinement: Callable[[], object],
    ) -> object:
        snapshot = self.snapshot()
        if (
            implementation_id != snapshot.implementation_id
            or source_hash != snapshot.source_hash
        ):
            raise ApiError(
                503,
                "weixin_orchestration_development_changed",
                "微信开发编排实现已变化，已受理任务未继续执行。",
            )
        spec = importlib.util.spec_from_file_location(
            f"chub_weixin_orchestration_dev_{snapshot.source_hash}",
            self.source_path,
        )
        if spec is None or spec.loader is None:
            raise ApiError(
                503,
                "weixin_orchestration_development_unavailable",
                "微信开发编排实现当前不可用，本次任务未执行。",
            )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            execute = getattr(module, "execute_refinement")
            if not callable(execute):
                raise TypeError("development entry is unavailable")
            return execute(enqueue_refinement=enqueue_refinement)
        except (OSError, ImportError, TypeError, AttributeError) as exc:
            raise ApiError(
                503,
                "weixin_orchestration_development_unavailable",
                "微信开发编排实现当前不可用，本次任务未执行。",
            ) from exc

    def require_snapshot(
        self,
        implementation_id: str | None,
        source_hash: str | None,
    ) -> None:
        snapshot = self.snapshot()
        if (
            implementation_id != snapshot.implementation_id
            or source_hash != snapshot.source_hash
        ):
            raise ApiError(
                503,
                "weixin_orchestration_development_changed",
                "微信开发编排实现已变化，已受理任务未继续执行。",
            )
