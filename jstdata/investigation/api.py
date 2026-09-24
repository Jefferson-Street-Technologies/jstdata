"""Investigation domain API (create/load + V1 mutation helpers).

CLI and (later) TUI command mode call into this module rather than
manipulating YAML ad hoc.
"""

from __future__ import annotations

import json
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
    validate_type: bool = True,
) -> Action:
    """Add a child action. Default parent is the current active action."""
    if validate_type:
        validate_action_type(action_type)
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
    force: bool = False,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> list[int]:
    """Cascade-delete an action and its descendants.

    ``force`` is required when the action has children, or when it is the
    active action of the active investigation.
    """
    if action_id == ROOT_ACTION_ID:
        raise InvestigationError("Cannot remove the root action.")
    if action_id not in inv.actions:
        raise InvestigationError(
            f"Unknown action {action_id} in investigation {inv.id!r}."
        )

    subtree = inv.subtree_ids(action_id)
    has_children = len(subtree) > 1
    ctx = try_load_active(app_dir=app_dir)
    is_active = bool(
        ctx and ctx.investigation == inv.id and ctx.action == action_id
    )
    if (has_children or is_active) and not force:
        reasons: list[str] = []
        if has_children:
            reasons.append(
                f"action {action_id} has {len(subtree) - 1} descendant(s)"
            )
        if is_active:
            reasons.append("action is the active action")
        raise InvestigationError(
            "Refusing to remove: "
            + "; ".join(reasons)
            + ". Pass --force to confirm."
        )

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


def require_active_investigation(
    *, app_dir: Optional[Path] = None
) -> tuple[Investigation, ActiveContext]:
    """Load the investigation named by ``.active.yaml``."""
    ensure_bootstrap(app_dir=app_dir)
    ctx = get_active(app_dir=app_dir)
    inv = get_investigation(ctx.investigation, app_dir=app_dir)
    return inv, ctx


def parse_action_args_json(raw: Optional[str]) -> dict[str, Any]:
    """Parse the ``--args`` JSON object (default empty)."""
    if raw is None or raw.strip() == "":
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvestigationError(f"Invalid --args JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise InvestigationError("--args must be a JSON object.")
    return data


def validate_action_type(action_type: str) -> str:
    """Ensure ``action_type`` is a registered action id."""
    import jstdata.actions  # noqa: F401
    import jstdata.investigation.home  # noqa: F401

    from ..actions.base import get_action

    try:
        get_action(action_type)
    except KeyError as exc:
        raise InvestigationError(str(exc)) from exc
    return action_type


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
    "parse_action_args_json",
    "persist_investigation",
    "remove_action",
    "require_active_investigation",
    "set_active",
    "validate_action_type",
]
