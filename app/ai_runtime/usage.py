from app.ai_usage.models import AiUsageData
from app.ai_runtime import RuntimeOperationError, RuntimeRegistry


class RuntimeUsageService:
    """Routes shared usage reads to the Runtime that owns the collection."""

    def __init__(self, registry: RuntimeRegistry, *, default_runtime_id: str) -> None:
        self._registry = registry
        self._default_runtime_id = default_runtime_id

    def read(self, *, force: bool = False, runtime_id: str | None = None) -> AiUsageData:
        selected_runtime_id = runtime_id or self._default_runtime_id
        adapter = self._registry.require(selected_runtime_id, {"usage_snapshot"})
        reader = adapter
        data = reader.read_usage_snapshot(force=force)
        if data.runtime_id != selected_runtime_id:
            raise RuntimeOperationError(
                "runtime_usage_invalid",
                "Runtime usage snapshot does not match its owner",
                kind="conflict",
            )
        return data
