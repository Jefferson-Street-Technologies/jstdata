"""Investigations: persistent human–agent research artifacts."""

from .active import ActiveContext, active_path, load_active, save_active
from .api import (
    add_action,
    create_investigation,
    get_active,
    get_investigation,
    open_investigation,
    parse_action_args_json,
    persist_investigation,
    remove_action,
    require_active_investigation,
    set_active,
    sync_workspace_from_session,
    validate_action_type,
)
from .bootstrap import ensure_bootstrap, load_bundled_tutorial
from .host import InvestigationHost, run_investigation_host
from .model import (
    DEFAULT_WORKSPACE_ID,
    ROOT_ACTION_ID,
    Action,
    Investigation,
    InvestigationError,
    Workspace,
)
from .store import (
    TUTORIAL_ID,
    investigation_exists,
    investigation_path,
    list_investigations,
    load_investigation,
    save_investigation,
    validate_investigation_id,
)
from . import home as _home  # noqa: F401 — register home step

__all__ = [
    "Action",
    "ActiveContext",
    "DEFAULT_WORKSPACE_ID",
    "Investigation",
    "InvestigationError",
    "InvestigationHost",
    "ROOT_ACTION_ID",
    "TUTORIAL_ID",
    "Workspace",
    "active_path",
    "add_action",
    "create_investigation",
    "ensure_bootstrap",
    "get_active",
    "get_investigation",
    "investigation_exists",
    "investigation_path",
    "list_investigations",
    "load_active",
    "load_bundled_tutorial",
    "load_investigation",
    "open_investigation",
    "parse_action_args_json",
    "persist_investigation",
    "remove_action",
    "require_active_investigation",
    "run_investigation_host",
    "save_active",
    "save_investigation",
    "set_active",
    "sync_workspace_from_session",
    "validate_action_type",
    "validate_investigation_id",
]
