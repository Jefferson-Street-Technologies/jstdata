"""Investigation domain API (create/load + V1 mutation helpers).

CLI and (later) TUI command mode call into this module rather than
manipulating YAML ad hoc.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..client import JSTDataClient

from .active import ActiveContext, load_active, save_active, try_load_active
from .bootstrap import ensure_bootstrap
from .model import (
    DEFAULT_WORKSPACE_ID,
    Action,
    Investigation,
    InvestigationError,
    Query,
    ResearchContext,
    ROOT_ACTION_ID,
    Workspace,
)
from .store import (
    allocate_investigation_id,
    investigation_exists,
    list_investigations,
    load_investigation,
    save_investigation,
    validate_investigation_id,
)


def _inv_root(app_dir: Optional[Path]) -> Optional[Path]:
    return None if app_dir is None else app_dir / "investigations"


def create_investigation(
    investigation_id: Optional[str] = None,
    *,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    app_dir: Optional[Path] = None,
    activate: bool = True,
    open_tui: bool = False,
) -> Investigation:
    """Create a new investigation on disk and optionally activate it.

    ``investigation_id`` is optional; a unique ``inv-<unix>`` slug is allocated
    when omitted. Title defaults to ``Untitled`` for generated ids, otherwise
    to the id. ``open_tui`` is ignored here — the CLI launches the host.
    """
    ensure_bootstrap(app_dir=app_dir)
    inv_root = _inv_root(app_dir)
    if investigation_id is None:
        investigation_id = allocate_investigation_id(root=inv_root)
        if title is None:
            title = "Untitled"
    else:
        validate_investigation_id(investigation_id)
        if investigation_exists(investigation_id, root=inv_root):
            raise InvestigationError(
                f"Investigation {investigation_id!r} already exists."
            )
    inv = Investigation.new(investigation_id, title=title, notes=notes)
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


def update_investigation(
    inv: Investigation,
    *,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    annotations: Optional[str] = None,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Investigation:
    """Set investigation title, description (``notes``), and/or annotations.

    ``title`` must be non-empty when provided. ``notes=""`` / ``annotations=""``
    clear those fields; ``None`` leaves them unchanged.
    """
    if title is not None:
        title = title.strip()
        if not title:
            raise InvestigationError("Investigation title cannot be empty.")
        inv.title = title
    if notes is not None:
        stripped = notes.strip()
        inv.notes = stripped or None
    if annotations is not None:
        stripped = annotations.strip()
        inv.annotations = stripped or None
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    return inv


def update_query(
    inv: Investigation,
    query_id: int,
    *,
    name: Optional[str] = None,
    annotations: Optional[str] = None,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Query:
    """Patch a saved query's display name and/or annotations.

    ``annotations=""`` clears; ``annotations=None`` leaves unchanged.
    """
    query = inv.get_query(query_id)
    if name is not None:
        stripped = name.strip()
        query.name = stripped or None
    if annotations is not None:
        stripped = annotations.strip()
        query.annotations = stripped or None
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    return query


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
    context: Optional[ResearchContext] = None,
    activate: bool = False,
    app_dir: Optional[Path] = None,
    persist: bool = True,
    validate_type: bool = True,
) -> Action:
    """Add a child action/view. Default parent is the current active action."""
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
        context=context,
    )
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    if activate:
        set_active(
            inv.id,
            workspace=DEFAULT_WORKSPACE_ID,
            action=action.id,
            app_dir=app_dir,
        )
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
            workspace=DEFAULT_WORKSPACE_ID,
            action=ROOT_ACTION_ID,
            app_dir=app_dir,
        )
    return removed


def add_query(
    inv: Investigation,
    *,
    name: Optional[str] = None,
    annotations: Optional[str] = None,
    notes: Optional[str] = None,
    metric: Optional[list[str]] = None,
    entity: Optional[list[str]] = None,
    series: Optional[list[str]] = None,
    frequency: Optional[str] = None,
    units: Optional[str] = None,
    source: Optional[str] = None,
    seasonal_adjustment: Optional[str] = None,
    taxonomy: Optional[str] = None,
    head: Optional[int] = None,
    tail: Optional[int] = None,
    as_of: Optional[str] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    from_workspace: bool = False,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Query:
    """Add a saved query to the investigation (cartesian metric × entity).

    ``notes`` is accepted as a legacy alias for ``annotations``.
    """
    if annotations is None and notes is not None:
        annotations = notes
    query = inv.add_query(
        name=name,
        annotations=annotations,
        metric=metric,
        entity=entity,
        series=series,
        frequency=frequency,
        units=units,
        source=source,
        seasonal_adjustment=seasonal_adjustment,
        taxonomy=taxonomy,
        head=head,
        tail=tail,
        as_of=as_of,
        sort_by=sort_by,
        order_by=order_by,
        from_workspace=from_workspace,
    )
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    return query


def remove_query(
    inv: Investigation,
    query_id: int,
    *,
    app_dir: Optional[Path] = None,
    persist: bool = True,
) -> Query:
    """Delete a saved query. Also drops query views that point at it."""
    query = inv.remove_query(query_id)
    stale = [
        action.id
        for action in list(inv.actions.values())
        if action.type == "query"
        and action.args.get("query_id") == query_id
        and action.id != ROOT_ACTION_ID
    ]
    for action_id in stale:
        try:
            inv.remove_action_cascade(action_id)
        except InvestigationError:
            continue
    if persist:
        persist_investigation(inv, app_dir=app_dir)
    ctx = try_load_active(app_dir=app_dir)
    if ctx and ctx.investigation == inv.id and ctx.action not in inv.actions:
        set_active(
            inv.id,
            workspace=DEFAULT_WORKSPACE_ID,
            action=ROOT_ACTION_ID,
            app_dir=app_dir,
        )
    return query


def inspect_query(
    inv: Investigation,
    query_id: int,
    *,
    client: JSTDataClient,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Coverage for a saved query's cartesian product (no observations)."""
    return inv.get_query(query_id).inspect(client, limit=limit, offset=offset)


def run_query(
    inv: Investigation,
    query_id: int,
    *,
    client: JSTDataClient,
    **overrides: Any,
) -> list:
    """Execute a saved query against ``GET /query``."""
    return inv.get_query(query_id).execute(client, **overrides)


def resume_or_create_investigation(
    *, app_dir: Optional[Path] = None
) -> Investigation:
    """Resume ``.active.yaml``, or create an Untitled investigation if none."""
    ensure_bootstrap(app_dir=app_dir)
    ctx = try_load_active(app_dir=app_dir)
    if ctx is not None:
        try:
            return open_investigation(ctx.investigation, app_dir=app_dir)
        except InvestigationError:
            pass
    return create_investigation(app_dir=app_dir)


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
    "Query",
    "Workspace",
    "add_action",
    "add_query",
    "create_investigation",
    "ensure_bootstrap",
    "get_active",
    "get_investigation",
    "inspect_query",
    "list_investigations",
    "open_investigation",
    "parse_action_args_json",
    "persist_investigation",
    "remove_action",
    "remove_query",
    "require_active_investigation",
    "resume_or_create_investigation",
    "run_query",
    "set_active",
    "update_investigation",
    "update_query",
    "validate_action_type",
]
