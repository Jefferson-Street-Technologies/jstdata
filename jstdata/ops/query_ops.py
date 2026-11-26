"""Logged query execute / save / show / inspect / remove."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Sequence

from ..client import JSTDataClient
from ..investigation.api import (
    add_query,
    inspect_query,
    persist_investigation,
    remove_query,
    run_query,
    update_query,
)
from ..investigation.model import Query, QueryRemark
from .result import OperationError
from .runner import run_operation
from .serialize import jsonable, serialize_timeseries
from .targeting import resolve_target

_SPEC_KEYS = (
    "metric",
    "entity",
    "series",
    "frequency",
    "taxonomy",
    "head",
    "tail",
    "as_of",
    "sort_by",
    "order_by",
    "units",
    "source",
    "seasonal_adjustment",
    "name",
    "annotations",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value if v is not None and str(v)]


def _load_spec_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationError(f"Cannot read query spec {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise OperationError(f"Query spec {path} must be a JSON object.")
    return raw


def _spec_from_inputs(
    *,
    query_id: Optional[int],
    spec_path: Optional[Path],
    metric: Optional[Sequence[str]],
    entity: Optional[Sequence[str]],
    series: Optional[Sequence[str]],
    frequency: Optional[str],
    taxonomy: Optional[str],
    head: Optional[int],
    tail: Optional[int],
    as_of: Optional[str],
    sort_by: Optional[str],
    order_by: Optional[str],
    name: Optional[str],
    target_inv,
) -> tuple[dict[str, Any], Optional[Query]]:
    """Build a query-shaped dict and optional existing Query object."""
    if query_id is not None:
        query = target_inv.get_query(int(query_id))
        return query.to_dict(), query

    merged: dict[str, Any] = {}
    if spec_path is not None:
        merged.update(_load_spec_file(Path(spec_path)))

    overrides = {
        "metric": _as_list(metric) or None,
        "entity": _as_list(entity) or None,
        "series": _as_list(series) or None,
        "frequency": frequency,
        "taxonomy": taxonomy,
        "head": head,
        "tail": tail,
        "as_of": as_of,
        "sort_by": sort_by,
        "order_by": order_by,
        "name": name,
    }
    for key, value in overrides.items():
        if value is not None:
            merged[key] = value

    # Normalize list fields.
    for key in ("metric", "entity", "series"):
        if key in merged:
            merged[key] = _as_list(merged.get(key))

    if not (merged.get("metric") or merged.get("entity") or merged.get("series")):
        raise OperationError(
            "Query run needs --query, --spec, or --metric/--entity/--series."
        )
    return merged, None


def run_query_op(
    client: JSTDataClient,
    *,
    query_id: Optional[int] = None,
    spec: Optional[Path] = None,
    metric: Optional[Sequence[str]] = None,
    entity: Optional[Sequence[str]] = None,
    series: Optional[Sequence[str]] = None,
    frequency: Optional[str] = None,
    taxonomy: Optional[str] = None,
    head: Optional[int] = None,
    tail: Optional[int] = None,
    as_of: Optional[str] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    save: bool = False,
    name: Optional[str] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
    use_cache: bool = True,
):
    """Execute a query against the Data API and log an immutable artifact.

    With ``save=True``, persist an exploratory Query (``in_report=False``).
    """
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None

    inputs: dict[str, Any] = {
        "query_id": query_id,
        "spec": str(spec) if spec else None,
        "metric": _as_list(metric) or None,
        "entity": _as_list(entity) or None,
        "series": _as_list(series) or None,
        "frequency": frequency,
        "taxonomy": taxonomy,
        "head": head,
        "tail": tail,
        "as_of": as_of,
        "sort_by": sort_by,
        "order_by": order_by,
        "save": save,
        "name": name,
    }

    def _run():
        spec_dict, existing = _spec_from_inputs(
            query_id=query_id,
            spec_path=spec,
            metric=metric,
            entity=entity,
            series=series,
            frequency=frequency,
            taxonomy=taxonomy,
            head=head,
            tail=tail,
            as_of=as_of,
            sort_by=sort_by,
            order_by=order_by,
            name=name,
            target_inv=target.investigation,
        )
        # Drop non-API keys from execute kwargs.
        exec_kwargs = {
            k: v
            for k, v in spec_dict.items()
            if k
            in {
                "metric",
                "entity",
                "series",
                "frequency",
                "taxonomy",
                "head",
                "tail",
                "as_of",
                "sort_by",
                "order_by",
            }
            and v is not None
        }
        if existing is not None:
            rows = run_query(target.investigation, existing.id, client=client)
        else:
            tmp = Query(id=0, **{k: v for k, v in exec_kwargs.items() if k in Query.__dataclass_fields__})
            rows = tmp.execute(client)

        saved_query_id = None
        query_payload = dict(spec_dict)
        if save and existing is None:
            q = add_query(
                target.investigation,
                name=name or query_payload.get("name"),
                annotations=query_payload.get("annotations"),
                metric=query_payload.get("metric"),
                entity=query_payload.get("entity"),
                series=query_payload.get("series"),
                frequency=query_payload.get("frequency"),
                taxonomy=query_payload.get("taxonomy"),
                head=query_payload.get("head"),
                tail=query_payload.get("tail"),
                as_of=query_payload.get("as_of"),
                sort_by=query_payload.get("sort_by"),
                order_by=query_payload.get("order_by"),
                units=query_payload.get("units"),
                source=query_payload.get("source"),
                seasonal_adjustment=query_payload.get("seasonal_adjustment"),
                app_dir=app_dir,
                persist=False,
            )
            q.in_report = False
            persist_investigation(target.investigation, app_dir=app_dir)
            saved_query_id = q.id
            query_payload = q.to_dict()
        elif existing is not None:
            query_payload = existing.to_dict()
            saved_query_id = existing.id

        query_payload["in_report"] = bool(query_payload.get("in_report", False))
        return {
            "query": query_payload,
            "records": serialize_timeseries(rows),
            "record_count": len(rows),
            "saved_query_id": saved_query_id,
        }

    return run_operation(
        "query.run",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=use_cache and not save,
    )


def show_query_op(
    query_id: int,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    def _run():
        target = resolve_target(
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            require=True,
        )
        assert target is not None
        q = target.investigation.get_query(int(query_id))
        return {
            "query": q.to_dict(),
            "_target": {
                "project_id": target.project_id,
                "investigation_id": target.investigation_id,
            },
        }

    result = run_operation(
        "query.show",
        _run,
        inputs={"query_id": query_id},
        app_dir=app_dir,
        use_cache=False,
    )
    if result.status == "success" and isinstance(result.data, dict):
        meta = result.data.pop("_target", None)
        if meta:
            result.project_id = meta["project_id"]
            result.investigation_id = meta["investigation_id"]
        result.data = result.data.get("query", result.data)
    return result


def inspect_query_op(
    client: JSTDataClient,
    query_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None

    def _run():
        return jsonable(
            inspect_query(
                target.investigation,
                int(query_id),
                client=client,
                limit=limit,
                offset=offset,
            )
        )

    return run_operation(
        "query.inspect",
        _run,
        inputs={
            "query_id": query_id,
            "limit": limit,
            "offset": offset,
        },
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def add_query_op(
    *,
    metric: Optional[Sequence[str]] = None,
    entity: Optional[Sequence[str]] = None,
    series: Optional[Sequence[str]] = None,
    frequency: Optional[str] = None,
    taxonomy: Optional[str] = None,
    head: Optional[int] = None,
    tail: Optional[int] = None,
    as_of: Optional[str] = None,
    sort_by: Optional[str] = None,
    order_by: Optional[str] = None,
    name: Optional[str] = None,
    annotations: Optional[str] = None,
    in_report: bool = False,
    from_workspace: bool = False,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    """Persist a query definition (default exploratory / not in report)."""
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    inputs = {
        "metric": _as_list(metric) or None,
        "entity": _as_list(entity) or None,
        "series": _as_list(series) or None,
        "frequency": frequency,
        "taxonomy": taxonomy,
        "head": head,
        "tail": tail,
        "as_of": as_of,
        "sort_by": sort_by,
        "order_by": order_by,
        "name": name,
        "annotations": annotations,
        "in_report": in_report,
        "from_workspace": from_workspace,
    }

    def _run():
        q = add_query(
            target.investigation,
            name=name,
            annotations=annotations,
            metric=list(metric or []) if metric else None,
            entity=list(entity or []) if entity else None,
            series=list(series or []) if series else None,
            frequency=frequency,
            taxonomy=taxonomy,
            head=head,
            tail=tail,
            as_of=as_of,
            sort_by=sort_by,
            order_by=order_by,
            from_workspace=from_workspace,
            app_dir=app_dir,
            persist=False,
        )
        q.in_report = bool(in_report)
        persist_investigation(target.investigation, app_dir=app_dir)
        return {"query": q.to_dict(), "query_id": q.id}

    return run_operation(
        "query.add",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def remove_query_op(
    query_id: int,
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

    def _run():
        q = remove_query(
            target.investigation, int(query_id), app_dir=app_dir
        )
        return {"removed_query_id": q.id, "name": q.name}

    return run_operation(
        "query.remove",
        _run,
        inputs={"query_id": query_id},
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def patch_query_op(
    query_id: int,
    *,
    add_metric: Optional[Sequence[str]] = None,
    add_entity: Optional[Sequence[str]] = None,
    add_series: Optional[Sequence[str]] = None,
    remove_metric: Optional[Sequence[str]] = None,
    remove_entity: Optional[Sequence[str]] = None,
    remove_series: Optional[Sequence[str]] = None,
    frequency: Optional[str] = None,
    clear_frequency: bool = False,
    head: Optional[int] = None,
    tail: Optional[int] = None,
    clear_window: bool = False,
    name: Optional[str] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    """Add/remove composition ids or tweak scope on an existing query."""
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    add_m = _as_list(add_metric)
    add_e = _as_list(add_entity)
    add_s = _as_list(add_series)
    rm_m = set(_as_list(remove_metric))
    rm_e = set(_as_list(remove_entity))
    rm_s = set(_as_list(remove_series))
    inputs = {
        "query_id": int(query_id),
        "add_metric": add_m or None,
        "add_entity": add_e or None,
        "add_series": add_s or None,
        "remove_metric": sorted(rm_m) or None,
        "remove_entity": sorted(rm_e) or None,
        "remove_series": sorted(rm_s) or None,
        "frequency": frequency,
        "clear_frequency": clear_frequency or None,
        "head": head,
        "tail": tail,
        "clear_window": clear_window or None,
        "name": name,
    }
    if not any(
        [
            add_m,
            add_e,
            add_s,
            rm_m,
            rm_e,
            rm_s,
            frequency is not None,
            clear_frequency,
            head is not None,
            tail is not None,
            clear_window,
            name is not None,
        ]
    ):
        raise OperationError(
            "query patch needs --add-* / --remove-* / scope flags / --name."
        )

    def _run():
        q = target.investigation.get_query(int(query_id))
        before = q.to_dict()
        for mid in add_m:
            if mid not in q.metric:
                q.metric = list(q.metric) + [mid]
        for eid in add_e:
            if eid not in q.entity:
                q.entity = list(q.entity) + [eid]
        for sid in add_s:
            if sid not in q.series:
                q.series = list(q.series) + [sid]
        if rm_m:
            q.metric = [x for x in q.metric if x not in rm_m]
        if rm_e:
            q.entity = [x for x in q.entity if x not in rm_e]
        if rm_s:
            q.series = [x for x in q.series if x not in rm_s]
        if clear_frequency:
            q.frequency = None
        elif frequency is not None:
            q.frequency = frequency
        if clear_window:
            q.head = None
            q.tail = None
        elif head is not None:
            q.head = head
            if head:
                q.tail = None
        elif tail is not None:
            q.tail = tail
            if tail:
                q.head = None
        if name is not None:
            stripped = name.strip()
            q.name = stripped or None
        persist_investigation(target.investigation, app_dir=app_dir)
        return {
            "query_id": q.id,
            "before": before,
            "query": q.to_dict(),
        }

    return run_operation(
        "query.patch",
        _run,
        inputs={k: v for k, v in inputs.items() if v is not None},
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def remark_add_op(
    query_id: int,
    text: str,
    *,
    name: Optional[str] = None,
    series: Optional[Sequence[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    """Append a :class:`QueryRemark` to a saved query."""
    body = (text or "").strip()
    if not body:
        raise OperationError("remark add requires non-empty --text.")
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    series_ids = _as_list(series)
    inputs = {
        "query_id": int(query_id),
        "text": body,
        "name": (name or "").strip() or None,
        "series": series_ids or None,
        "from": date_from,
        "to": date_to,
    }

    def _run():
        q = target.investigation.get_query(int(query_id))
        remark = QueryRemark(
            text=body,
            name=(name or "").strip() or None,
            series=list(series_ids),
            from_=date_from,
            to=date_to,
        )
        remarks = list(q.remarks or []) + [remark]
        update_query(
            target.investigation,
            int(query_id),
            remarks=remarks,
            app_dir=app_dir,
            persist=True,
        )
        q = target.investigation.get_query(int(query_id))
        return {
            "query_id": q.id,
            "index": len(q.remarks),
            "remark": remark.to_dict(),
            "remarks": [r.to_dict() for r in q.remarks],
        }

    return run_operation(
        "remark.add",
        _run,
        inputs={k: v for k, v in inputs.items() if v is not None},
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )


def remark_remove_op(
    query_id: int,
    index: int,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir: Optional[Path] = None,
):
    """Delete a remark by 1-based index."""
    if index < 1:
        raise OperationError("remark index must be 1-based (>= 1).")
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=True,
    )
    assert target is not None
    inputs = {"query_id": int(query_id), "index": int(index)}

    def _run():
        q = target.investigation.get_query(int(query_id))
        remarks = list(q.remarks or [])
        if index > len(remarks):
            raise OperationError(
                f"No remark {index} on query {query_id} ({len(remarks)} exist)."
            )
        removed = remarks.pop(index - 1)
        update_query(
            target.investigation,
            int(query_id),
            remarks=remarks,
            app_dir=app_dir,
            persist=True,
        )
        return {
            "query_id": int(query_id),
            "index": int(index),
            "removed": removed.to_dict(),
            "remarks": [r.to_dict() for r in remarks],
        }

    return run_operation(
        "remark.remove",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=False,
    )
