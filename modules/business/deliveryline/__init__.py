from pathlib import Path

from fastapi import FastAPI, Request

from app.core.business_modules import BusinessModuleDefinition
from app.core.config import PROJECT_ROOT, Settings

from .collaboration import DeliverylineCollaboration
from .store import DeliverylineStore
from .api import router
from .web import workspace_records, workspace_state

MODULE_ROOT = Path(__file__).resolve().parent
MODULE_STATIC_DIR = MODULE_ROOT / "static"
MODULE_TEMPLATE_DIR = MODULE_ROOT / "templates"
MODULE_SHARED_DATA_DIR = PROJECT_ROOT / "data" / "shared" / "deliveryline" / "requirements"
MODULE_STATE_DIR = PROJECT_ROOT / "data" / "local" / "state" / "deliveryline"


def _initialize(application: FastAPI, settings: Settings) -> None:
    application.state.deliveryline_store = DeliverylineStore(
        MODULE_SHARED_DATA_DIR,
        MODULE_STATE_DIR,
    )
    application.state.deliveryline_collaboration = DeliverylineCollaboration(
        MODULE_STATE_DIR,
    )


def _session_visibility_get(request: Request) -> bool:
    return request.app.state.deliveryline_collaboration.show_sessions()


def _session_visibility_set(request: Request, value: bool) -> None:
    request.app.state.deliveryline_collaboration.set_show_sessions(value)


def _hidden_session_ids(request: Request) -> set[str]:
    return request.app.state.deliveryline_collaboration.hidden_session_ids()


def _recovery_state_paths(settings: Settings) -> tuple[Path, ...]:
    return (MODULE_STATE_DIR,)


def create_business_module(
    settings: Settings,
    module_root: Path | None = None,
) -> BusinessModuleDefinition:
    root = module_root or MODULE_ROOT
    return BusinessModuleDefinition(
        module_id="deliveryline",
        name="Deliveryline",
        description="管理意图交付的长期上下文与可验收业务切片。",
        version="dev",
        root=root,
        template_dir=root / "templates",
        static_dir=root / "static",
        api_router=router,
        workspace_template="deliveryline_workspace.html",
        settings_template="deliveryline_settings.html",
        initialize=_initialize,
        workspace_state=workspace_state,
        workspace_records=workspace_records,
        session_visibility_get=_session_visibility_get,
        session_visibility_set=_session_visibility_set,
        hidden_session_ids=_hidden_session_ids,
        recovery_state_paths=_recovery_state_paths,
    )

__all__ = [
    "DeliverylineCollaboration",
    "DeliverylineStore",
    "create_business_module",
]
