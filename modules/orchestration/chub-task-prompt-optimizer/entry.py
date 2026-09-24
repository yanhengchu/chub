"""Minimal descriptor entry; task-stage execution is a later delivery item."""

from app.plugin_lifecycle.orchestration_loader import OrchestrationPluginDescriptor


def create_orchestration_plugin() -> OrchestrationPluginDescriptor:
    """Return host metadata only; this delivery does not register or run stages."""
    return OrchestrationPluginDescriptor(
        module_id="chub-task-prompt-optimizer",
        version="0.1.0",
        scope="ordinary_user_task",
    )
