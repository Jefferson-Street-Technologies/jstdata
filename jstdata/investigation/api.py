"""Barebones Python API for investigations (V0).

Human/agent live mutation arrives in V1 (CLI) and V2 (TUI). This module is the
shared domain surface those interfaces will call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .active import ActiveContext, load_active, save_active, try_load_active
from .bootstrap import ensure_bootstrap
from .model import (
    DEFAULT_WORKSPACE_ID,
    Action,
    Investigation,
    InvestigationError,
    ROOT_ACTION_ID,
    Workspace,
)
from .store import (
    investigation_exists,
    list_investigations,
    load_investigation,
    save_investigation,
    validate_investigation_id,
)


def _inv_root(app_dir: Optional[Path]) -> Optional[Path]:
    return None if app_dir is None else app_dir / "investigations"


def create_investigation(
    investigation_id: str,
    *,
    title: Optional[str] = None,
    app_dir: Optional[Path] = None,
    activate: bool = True,
    open_tui: bool = False,
) -> Investigation:
    """Create a new investigation on disk and optionally activate it.

    ``open_tui`` is ignored here — the CLI launches the host after create.
    """
    ensure_bootstrap(app_dir=app_dir)
    validate_investigation_id(investigation_id)
    inv_root = _inv_root(app_dir)
    if investigation_exists(investigation_id, root=inv_root):
        raise InvestigationError(
            f"Investigation {investigation_id!r} already exists."
        )
    inv = Investigation.new(investigation_id, title=title)
    save_investigation(inv, root=inv_root)
    if activate:
        set_active(
            investigation_id,
            workspace=DEFAULT_WORKSPACE_ID,
            action=ROOT_ACTION_ID,
            app_dir=app_dir,
        )
    return inv


def open_investigation(
    investigation_id: str,
    *,
    app_dir: Optional[Path] = None,
    activate: bool = True,
) -> Investigation:
    """Load an existing investigation and optionally make it active."""
    ensure_bootstrap(app_dir=app_dir)
    inv_root = _inv_root(app_dir)
    inv = load_investigation(investigation_id, root=inv_root)
    if activate:
        # Prefer existing active action/workspace if already on this inv.
        current = try_load_active(app_dir=app_dir)
        workspace = DEFAULT_WORKSPACE_ID
        action = ROOT_ACTION_ID
        if current and current.investigation == investigation_id:
            if current.workspace in inv.workspaces:
                workspace = current.workspace
            if current.action in inv.actions:
                action = current.action
        set_active(
            investigation_id,
            workspace=workspace,
            action=action,
            app_dir=app_dir,
        )
    return inv


def get_investigation(
    investigation_id: str, *, app_dir: Optional[Path] = None
) -> Investigation:
    return load_investigation(investigation_id, root=_inv_root(app_dir))


def persist_investigation(
    inv: Investigation, *, app_dir: Optional[Path] = None
) -> Path:
    return save_investigation(inv, root=_inv_root(app_dir))


def get_active(*, app_dir: Optional[Path] = None) -> ActiveContext:
    return load_active(app_dir=app_dir)


def set_active(
    investigation: str,
    *,
    workspace: int = DEFAULT_WORKSPACE_ID,
    action: int = ROOT_ACTION_ID,
    app_dir: Optional[Path] = None,
) -> ActiveContext:
    ctx = ActiveContext(
        investigation=investigation,
        workspace=workspace,
        action=action,
    )
    save_active(ctx, app_dir=app_dir)
    return ctx


def add_action(
    inv: Investigation,
    action_type: str,
    *,
    parent_id: Optional[int] = None,
    args: Optional[dict[str, Any]] = None,
    description: Optional[str] = None,
    activate: bool = False,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Action:
    """Add a child action. Default parent is the current active action."""
    if parent_id is None:
        ctx = try_load_active(app_dir=app_dir)
        if ctx is None or ctx.investigation != inv.id:
            parent_id = ROOT_ACTION_ID
        else:
            parent_id = ctx.action if ctx.action in inv.actions else ROOT_ACTION_ID
    action = inv.add_action(
        action_type,
        parent_id=parent_id,
        args=args,
        description=description,
    )
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    if activate:
        ws = DEFAULT_WORKSPACE_ID
        ctx = try_load_active(app_dir=app_dir)
        if ctx and ctx.investigation == inv.id and ctx.workspace in inv.workspaces:
            ws = ctx.workspace
        set_active(inv.id, workspace=ws, action=action.id, app_dir=app_dir)
    return action


def remove_action(
    inv: Investigation,
    action_id: int,
    *,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> list[int]:
    """Cascade-delete an action and its descendants."""
    removed = inv.remove_action_cascade(action_id)
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    ctx = try_load_active(app_dir=app_dir)
    if ctx and ctx.investigation == inv.id and ctx.action in removed:
        set_active(
            inv.id,
            workspace=ctx.workspace
            if ctx.workspace in inv.workspaces
            else DEFAULT_WORKSPACE_ID,
            action=ROOT_ACTION_ID,
            app_dir=app_dir,
        )
    return removed


def sync_workspace_from_session(
    inv: Investigation,
    workspace_id: int,
    session,
    *,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Workspace:
    """Write live Session selectors back into a workspace."""
    ws = inv.get_workspace(workspace_id)
    ws.apply_session(session)
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    return ws


__all__ = [
    "Action",
    "ActiveContext",
    "Investigation",
    "InvestigationError",
    "Workspace",
    "add_action",
    "create_investigation",
    "ensure_bootstrap",
    "get_active",
    "get_investigation",
    "list_investigations",
    "open_investigation",
    "persist_investigation",
    "remove_action",
    "set_active",
    "sync_workspace_from_session",
]
