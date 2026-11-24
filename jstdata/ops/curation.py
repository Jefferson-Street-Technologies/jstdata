"""Promote exploratory results and copy research across investigations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from ..investigation.api import add_query, persist_investigation
from ..investigation.model import Investigation
from ..investigation.project_store import (
    load_project,
    load_project_investigation,
)
from .logstore import ActionLogStore
from .result import OperationError, OperationResult
from .runner import get_store, run_operation
from .targeting import resolve_target


def _query_from_event_payload(
    data: Any, inputs: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Best-effort extract a query-shaped dict from an action-log event."""
    if isinstance(data, dict):
        if "metric" in data or "entity" in data or "series" in data:
            return data
        nested = data.get("query")
        if isinstance(nested, dict):
            return nested
    # Specs sometimes live only in inputs.
    for key in ("spec", "query", "query_spec"):
        raw = inputs.get(key)
        if isinstance(raw, dict) and (
            raw.get("metric") or raw.get("entity") or raw.get("series")
        ):
            return raw
    return None


def promote_event(
    event_id: str,
    *,
    name: Optional[str] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
    store: Optional[ActionLogStore] = None,
) -> OperationResult:
    """Promote a logged event into a report query (no Data API re-fetch)."""

    def _run():
        log = store or get_store(app_dir=app_dir)
        try:
            event = log.get_event(event_id)
        except KeyError as exc:
            raise OperationError(str(exc)) from exc

        target = resolve_target(
            project=project or event.get("project_id"),
            investigation=investigation or event.get("investigation_id"),
            app_dir=app_dir,
            require=True,
        )
        assert target is not None
        inv = target.investigation

        artifact = event.get("artifact") or {}
        data = artifact.get("data")
        inputs = event.get("inputs") or {}
        if isinstance(inputs, str):
            import json

            try:
                inputs = json.loads(inputs)
            except json.JSONDecodeError:
                inputs = {}

        inputs_dict = inputs if isinstance(inputs, dict) else {}
        spec = _query_from_event_payload(data, inputs_dict)
        if spec is None:
            # Fall back: stage resource ids from discovery artifacts into workspace.
            ids: list[str] = []
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict) and row.get("id"):
                        ids.append(str(row["id"]))
            if not ids:
                raise OperationError(
                    f"Event {event_id!r} has no query spec or resource ids "
                    "to promote. Use a query-shaped event or "
                    "`jst actions promote --query <id>`."
                )
            op = str(event.get("operation") or "")
            as_entity = "entities" in op or "relationships" in op
            added: list[str] = []
            for rid in ids:
                if as_entity:
                    if inv.workspace.add_entity(rid):
                        added.append(rid)
                else:
                    if inv.workspace.add_metric(rid):
                        added.append(rid)
            persist_investigation(inv, app_dir=app_dir)
            return {
                "mode": "workspace",
                "event_id": event_id,
                "added": added,
                "as": "entity" if as_entity else "metric",
                "project_id": target.project_id,
                "investigation_id": target.investigation_id,
            }

        metric = list(spec.get("metric") or [])
        entity = list(spec.get("entity") or [])
        series = list(spec.get("series") or [])
        if not (metric or entity or series):
            raise OperationError(
                f"Event {event_id!r} query spec is empty."
            )
        qname = name or spec.get("name") or f"promoted from {event_id}"
        query = add_query(
            inv,
            name=qname,
            annotations=spec.get("annotations"),
            metric=metric or None,
            entity=entity or None,
            series=series or None,
            frequency=spec.get("frequency"),
            units=spec.get("units"),
            source=spec.get("source"),
            seasonal_adjustment=spec.get("seasonal_adjustment"),
            taxonomy=spec.get("taxonomy"),
            head=spec.get("head"),
            tail=spec.get("tail"),
            as_of=spec.get("as_of"),
            sort_by=spec.get("sort_by"),
            order_by=spec.get("order_by"),
            app_dir=app_dir,
            persist=False,
        )
        query.in_report = True
        if spec.get("notes_on"):
            from ..investigation.model import QueryNote

            notes = []
            for raw in spec["notes_on"]:
                if isinstance(raw, dict):
                    notes.append(QueryNote.from_dict(raw, label=qname))
            query.notes_on = notes
        persist_investigation(inv, app_dir=app_dir)
        return {
            "mode": "query",
            "event_id": event_id,
            "query_id": query.id,
            "in_report": True,
            "artifact_id": event.get("artifact_id"),
            "project_id": target.project_id,
            "investigation_id": target.investigation_id,
            "query": query.to_dict(),
        }

    return run_operation(
        "curation.promote",
        _run,
        inputs={"event_id": event_id, "name": name},
        app_dir=app_dir,
        use_cache=False,
        store=store,
    )


def promote_query(
    query_id: int,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
) -> OperationResult:
    """Mark an existing saved query as report membership."""

    def _run():
        target = resolve_target(
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            require=True,
        )
        assert target is not None
        inv = target.investigation
        query = inv.get_query(query_id)
        query.in_report = True
        persist_investigation(inv, app_dir=app_dir)
        return {
            "mode": "query",
            "query_id": query.id,
            "in_report": True,
            "project_id": target.project_id,
            "investigation_id": target.investigation_id,
            "query": query.to_dict(),
        }

    return run_operation(
        "curation.promote_query",
        _run,
        inputs={"query_id": query_id},
        app_dir=app_dir,
        use_cache=False,
    )


def copy_across_investigations(
    *,
    project_id: str,
    source_id: str,
    dest_id: str,
    metric: Optional[Sequence[str]] = None,
    entity: Optional[Sequence[str]] = None,
    series: Optional[Sequence[str]] = None,
    query_ids: Optional[Sequence[int]] = None,
    app_dir: Optional[Path] = None,
) -> OperationResult:
    """Copy workspace ids and/or queries between tabs (no Data API calls)."""

    def _run():
        project = load_project(project_id, app_dir=app_dir)
        if source_id not in project.investigations:
            raise OperationError(
                f"Source investigation {source_id!r} not in project {project_id!r}."
            )
        if dest_id not in project.investigations:
            raise OperationError(
                f"Destination investigation {dest_id!r} not in project {project_id!r}."
            )
        if source_id == dest_id:
            raise OperationError("Source and destination must differ.")
        src = load_project_investigation(
            project_id, source_id, app_dir=app_dir
        )
        dst = load_project_investigation(
            project_id, dest_id, app_dir=app_dir
        )
        added: dict[str, list[Any]] = {
            "metric": [],
            "entity": [],
            "series": [],
            "queries": [],
        }
        for mid in metric or ():
            if dst.workspace.add_metric(mid):
                added["metric"].append(mid)
        for eid in entity or ():
            if dst.workspace.add_entity(eid):
                added["entity"].append(eid)
        for sid in series or ():
            if dst.workspace.add_series(sid):
                added["series"].append(sid)
        for qid in query_ids or ():
            src_q = src.get_query(int(qid))
            payload = src_q.to_dict()
            payload.pop("id", None)
            new_q = add_query(
                dst,
                name=src_q.name,
                annotations=src_q.annotations,
                metric=list(src_q.metric) or None,
                entity=list(src_q.entity) or None,
                series=list(src_q.series) or None,
                frequency=src_q.frequency,
                units=src_q.units,
                source=src_q.source,
                seasonal_adjustment=src_q.seasonal_adjustment,
                taxonomy=src_q.taxonomy,
                head=src_q.head,
                tail=src_q.tail,
                as_of=src_q.as_of,
                sort_by=src_q.sort_by,
                order_by=src_q.order_by,
                app_dir=app_dir,
                persist=False,
            )
            new_q.in_report = src_q.in_report
            new_q.notes_on = list(src_q.notes_on)
            added["queries"].append(
                {"from": src_q.id, "to": new_q.id, "name": new_q.name}
            )
        persist_investigation(dst, app_dir=app_dir)
        return {
            "project_id": project_id,
            "source": source_id,
            "destination": dest_id,
            "added": added,
        }

    return run_operation(
        "curation.copy",
        _run,
        inputs={
            "project_id": project_id,
            "source_id": source_id,
            "dest_id": dest_id,
            "metric": list(metric or ()),
            "entity": list(entity or ()),
            "series": list(series or ()),
            "query_ids": list(query_ids or ()),
        },
        app_dir=app_dir,
        use_cache=False,
    )


def publication_projection(inv: Investigation) -> dict[str, Any]:
    """Serialize an investigation for publish: report queries only, no workspace."""
    data = inv.to_dict()
    # Drop private workspace staging from the wire body.
    data.pop("workspace", None)
    queries = []
    for q in sorted(inv.queries.values(), key=lambda x: x.id):
        if q.in_report:
            queries.append(q.to_dict())
    data["queries"] = queries
    return data
