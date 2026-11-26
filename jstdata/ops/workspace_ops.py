"""Workspace mutation and research-state helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from ..investigation.api import (
    persist_investigation,
    update_investigation,
    update_query,
)
from ..investigation.model import QueryNote
from .result import OperationError
from .runner import get_store, run_operation
from .targeting import resolve_target


def workspace_add(
    *,
    metric: Optional[Sequence[str]] = None,
    entity: Optional[Sequence[str]] = None,
    series: Optional[Sequence[str]] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    inputs = {
        "metric": list(metric or ()),
        "entity": list(entity or ()),
        "series": list(series or ()),
    }
    if not any(inputs.values()):
        raise OperationError(
            "workspace add needs --metric, --entity, and/or --series."
        )

    def _run():
        ws = target.investigation.workspace
        added = {"metric": [], "entity": [], "series": []}
        for mid in inputs["metric"]:
            if ws.add_metric(mid):
                added["metric"].append(mid)
        for eid in inputs["entity"]:
            if ws.add_entity(eid):
                added["entity"].append(eid)
        for sid in inputs["series"]:
            if ws.add_series(sid):
                added["series"].append(sid)
        persist_investigation(target.investigation, app_dir=app_dir)
        return {
            "added": added,
            "workspace": {
                "metric": list(ws.metric),
                "entity": list(ws.entity),
                "series": list(ws.series),
            },
        }

    return run_operation(
        "workspace.add",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def workspace_rm(
    ids: Sequence[str],
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    id_list = [str(i) for i in ids if i]
    if not id_list:
        raise OperationError("workspace rm needs at least one resource id.")

    def _run():
        ws = target.investigation.workspace
        removed = []
        for rid in id_list:
            if ws.remove_id(rid):
                removed.append(rid)
        persist_investigation(target.investigation, app_dir=app_dir)
        return {
            "removed": removed,
            "workspace": {
                "metric": list(ws.metric),
                "entity": list(ws.entity),
                "series": list(ws.series),
            },
        }

    return run_operation(
        "workspace.rm",
        _run,
        inputs={"ids": id_list},
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def annotate_op(
    text: str,
    *,
    query_id: Optional[int] = None,
    series: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    """Set investigation/query commentary, or append a ``notes_on`` slice."""
    body = (text or "").strip()
    if not body:
        raise OperationError("annotate requires non-empty --text.")

    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    inputs = {
        "text": body,
        "query_id": query_id,
        "series": series,
        "from": date_from,
        "to": date_to,
    }

    def _run():
        inv = target.investigation
        if query_id is None:
            update_investigation(
                inv, annotations=body, app_dir=app_dir, persist=True
            )
            return {
                "scope": "investigation",
                "annotations": inv.annotations,
            }

        q = inv.get_query(int(query_id))
        if series or date_from or date_to:
            note = QueryNote(
                text=body,
                series=series,
                from_=date_from,
                to=date_to,
            )
            q.notes_on = list(q.notes_on) + [note]
            persist_investigation(inv, app_dir=app_dir)
            return {
                "scope": "notes_on",
                "query_id": q.id,
                "notes_on": [n.to_dict() for n in q.notes_on],
            }

        update_query(
            inv, int(query_id), annotations=body, app_dir=app_dir, persist=True
        )
        return {
            "scope": "query",
            "query_id": q.id,
            "annotations": q.annotations,
        }

    return run_operation(
        "annotate",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def state_show(
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
    recent: int = 5,
):
    """Snapshot project/investigation research state + recent events."""
    from .result import OperationResult

    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    inv = target.investigation
    ws = inv.workspace
    store = get_store(app_dir=app_dir)
    events = store.list_events(
        project_id=target.project_id,
        investigation_id=target.investigation_id,
        limit=max(1, int(recent)),
    )
    data = {
        "project_id": target.project_id,
        "investigation_id": target.investigation_id,
        "title": inv.title,
        "description": inv.notes,
        "annotations": inv.annotations,
        "workspace": {
            "metric": list(ws.metric),
            "entity": list(ws.entity),
            "series": list(ws.series),
            "taxonomy": ws.taxonomy,
        },
        "queries": [
            {
                "id": q.id,
                "name": q.name,
                "in_report": q.in_report,
                "metric": list(q.metric),
                "entity": list(q.entity),
                "series": list(q.series),
            }
            for q in sorted(inv.queries.values(), key=lambda x: x.id)
        ],
        "action_count": len(inv.actions),
        "recent_events": [
            {
                "event_id": e["event_id"],
                "operation": e["operation"],
                "status": e["status"],
                "completed_at": e.get("completed_at"),
            }
            for e in events
        ],
    }
    return OperationResult(
        operation="state.show",
        status="success",
        event_id="evt_state_show",
        data=data,
        project_id=target.project_id,
        investigation_id=target.investigation_id,
        inputs={"recent": recent},
    )
