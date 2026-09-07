from app.ai_usage.models import AiUsageData
from collections.abc import Callable

from app.ai_runtime import RuntimeOperationError, RuntimeRegistry


class RuntimeUsageService:
    """Routes shared usage reads to the Runtime that owns the collection."""

    def __init__(
        self,
        registry: RuntimeRegistry | Callable[[], RuntimeRegistry],
        *,
        default_runtime_id: str | Callable[[], str],
    ) -> None:
        self._registry = registry
        self._default_runtime_id = default_runtime_id

    def _default_id(self) -> str:
        value = (
            self._default_runtime_id()
            if callable(self._default_runtime_id)
            else self._default_runtime_id
        )
        if not isinstance(value, str) or not value:
            raise RuntimeOperationError(
                "runtime_default_implementation_unavailable",
                "The default Runtime implementation is unavailable.",
            )
        return value

    def read(self, *, force: bool = False, runtime_id: str | None = None) -> AiUsageData:
        selected_runtime_id = runtime_id or self._default_id()
        registry = self._registry() if callable(self._registry) else self._registry
        adapter = registry.require(selected_runtime_id, {"usage_snapshot"})
        reader = adapter
        data = reader.read_usage_snapshot(force=force)
        owner_runtime_id = getattr(getattr(adapter, "descriptor", None), "runtime_id", None)
        if not isinstance(owner_runtime_id, str):
            owner_runtime_id = selected_runtime_id
        if data.runtime_id != owner_runtime_id:
            raise RuntimeOperationError(
                "runtime_usage_invalid",
                "Runtime usage snapshot does not match its owner",
                kind="conflict",
            )
        return data

    def open_login_page(self, *, runtime_id: str | None = None) -> None:
        selected_runtime_id = runtime_id or self._default_id()
        registry = self._registry() if callable(self._registry) else self._registry
        adapter = registry.require(selected_runtime_id, {"usage_login_page"})
        opener = adapter
        opener.open_usage_login_page()

    def login_page_available(self, *, runtime_id: str | None = None) -> bool:
        selected_runtime_id = runtime_id or self._default_id()
        registry = self._registry() if callable(self._registry) else self._registry
        try:
            registry.require(selected_runtime_id, {"usage_login_page"})
        except RuntimeOperationError:
            return False
        return True
