"""Discovery operations: search, taxonomies, relationships."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Optional, Sequence

from ..client import JSTDataClient
from .result import OperationError
from .runner import run_operation
from .targeting import resolve_target


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _serialize(items: Sequence[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            out.append(_jsonable(item.model_dump()))
        elif hasattr(item, "dict") and callable(item.dict):
            out.append(_jsonable(item.dict()))
        elif is_dataclass(item) and not isinstance(item, type):
            out.append(_jsonable(asdict(item)))
        elif isinstance(item, dict):
            out.append(_jsonable(item))
        else:
            out.append({"id": getattr(item, "id", None), "repr": repr(item)})
    return out


def search_entities(
    client: JSTDataClient,
    query: Optional[str] = None,
    *,
    taxonomy: Optional[str] = None,
    metric: Optional[Sequence[str]] = None,
    mode: str = "union",
    relation: Optional[Sequence[str]] = None,
    relation_direction: Optional[str] = None,
    relation_taxonomy: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    inputs = {
        "query": query,
        "taxonomy": taxonomy,
        "metric": list(metric or ()),
        "mode": mode,
        "relation": list(relation or ()),
        "relation_direction": relation_direction,
        "relation_taxonomy": relation_taxonomy,
        "limit": limit,
        "offset": offset,
    }
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        kwargs: dict[str, Any] = {
            "query": query,
            "taxonomy": taxonomy,
            "limit": limit,
            "offset": offset,
        }
        if metric:
            kwargs["metric"] = list(metric)
            kwargs["mode"] = mode
        if relation:
            kwargs["relation"] = list(relation)
        # Prefer A's new params when the client supports them.
        if relation_direction is not None:
            kwargs["relation_direction"] = relation_direction
        if relation_taxonomy is not None:
            kwargs["relation_taxonomy"] = relation_taxonomy
        try:
            results = client.search_entities(**kwargs)
        except TypeError:
            # Older client without direction params — drop them and retry.
            kwargs.pop("relation_direction", None)
            kwargs.pop("relation_taxonomy", None)
            results = client.search_entities(**kwargs)
        return _serialize(results)

    return run_operation(
        "search.entities",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def search_metrics(
    client: JSTDataClient,
    query: Optional[str] = None,
    *,
    taxonomy: Optional[str] = None,
    entity: Optional[Sequence[str]] = None,
    mode: str = "union",
    limit: int = 20,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    inputs = {
        "query": query,
        "taxonomy": taxonomy,
        "entity": list(entity or ()),
        "mode": mode,
        "limit": limit,
        "offset": offset,
    }
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        kwargs: dict[str, Any] = {
            "query": query,
            "taxonomy": taxonomy,
            "entity": list(entity) if entity else None,
            "limit": limit,
            "offset": offset,
        }
        if entity:
            kwargs["mode"] = mode
        return _serialize(client.search_metrics(**kwargs))

    return run_operation(
        "search.metrics",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def list_taxonomies(
    client: JSTDataClient,
    *,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        return _serialize(client.list_taxonomies(limit=limit, offset=offset))

    return run_operation(
        "taxonomy.list",
        _run,
        inputs={"limit": limit, "offset": offset},
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def taxonomy_entities(
    client: JSTDataClient,
    taxonomy_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        return _serialize(
            client.get_taxonomy_entities(
                taxonomy_id, limit=limit, offset=offset
            )
        )

    return run_operation(
        "taxonomy.entities",
        _run,
        inputs={
            "taxonomy_id": taxonomy_id,
            "limit": limit,
            "offset": offset,
        },
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def entity_relationships(
    client: JSTDataClient,
    entity_id: str,
    *,
    direction: Optional[str] = None,
    relationship: Optional[str] = None,
    relation_taxonomy: Optional[str] = None,
    taxonomy: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    if relationship == "identity":
        raise OperationError(
            "identity is membership, not a relationship filter. "
            "Use taxonomy=… for membership."
        )
    inputs = {
        "entity_id": entity_id,
        "direction": direction,
        "relationship": relationship,
        "relation_taxonomy": relation_taxonomy,
        "taxonomy": taxonomy,
        "limit": limit,
        "offset": offset,
    }
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        kwargs: dict[str, Any] = {
            "entity_id": entity_id,
            "limit": limit,
            "offset": offset,
        }
        if direction is not None:
            kwargs["direction"] = direction
        if relationship is not None:
            kwargs["relationship"] = relationship
        if relation_taxonomy is not None:
            kwargs["relation_taxonomy"] = relation_taxonomy
        if taxonomy is not None:
            kwargs["taxonomy"] = taxonomy
        try:
            results = client.get_entity_relations(**kwargs)
        except TypeError:
            # Client not yet extended for A's filters.
            results = client.get_entity_relations(
                entity_id, limit=limit, offset=offset
            )
        return _serialize(results)

    return run_operation(
        "entity.relationships",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def get_entity_metadata(
    client: JSTDataClient,
    entity_id: str,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        entity = client.get_entity(entity_id)
        if hasattr(entity, "model_dump"):
            return entity.model_dump()
        if hasattr(entity, "dict"):
            return entity.dict()
        return {"id": getattr(entity, "id", entity_id)}

    return run_operation(
        "metadata.entity",
        _run,
        inputs={"entity_id": entity_id},
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def get_metric_metadata(
    client: JSTDataClient,
    metric_id: str,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        metric = client.get_metric(metric_id)
        if hasattr(metric, "model_dump"):
            return metric.model_dump()
        if hasattr(metric, "dict"):
            return metric.dict()
        return {"id": getattr(metric, "id", metric_id)}

    return run_operation(
        "metadata.metric",
        _run,
        inputs={"metric_id": metric_id},
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def get_series_metadata(
    client: JSTDataClient,
    series_id: str,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        series = client.get_series(series_id)
        return _jsonable(
            series.model_dump()
            if hasattr(series, "model_dump")
            else series.dict()
            if hasattr(series, "dict")
            else asdict(series)
            if is_dataclass(series)
            else {"id": getattr(series, "id", series_id)}
        )

    return run_operation(
        "metadata.series",
        _run,
        inputs={"series_id": series_id},
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def get_release_metadata(
    client: JSTDataClient,
    release_id: str,
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )

    def _run():
        release = client.get_release(release_id)
        return _jsonable(
            release.model_dump()
            if hasattr(release, "model_dump")
            else release.dict()
            if hasattr(release, "dict")
            else asdict(release)
            if is_dataclass(release)
            else {"id": getattr(release, "id", release_id)}
        )

    return run_operation(
        "metadata.release",
        _run,
        inputs={"release_id": release_id},
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def coverage_preview(
    client: JSTDataClient,
    *,
    metric: Optional[Sequence[str]] = None,
    entity: Optional[Sequence[str]] = None,
    limit: int = 50,
    offset: int = 0,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
    use_cache: bool = True,
):
    """Metric × entity coverage via ``GET /preview`` (no observations)."""
    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )
    metrics = list(metric or ())
    entities = list(entity or ())
    inputs = {
        "metric": metrics,
        "entity": entities,
        "limit": limit,
        "offset": offset,
    }

    def _run():
        rows = client.preview(
            metric=metrics or None,
            entity=entities or None,
            limit=limit,
            offset=offset,
        )
        return _serialize(rows)

    return run_operation(
        "coverage.preview",
        _run,
        inputs=inputs,
        target=target,
        app_dir=app_dir,
        use_cache=use_cache,
    )


def workspace_show(
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
):
    def _run():
        target = resolve_target(
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            require=True,
        )
        assert target is not None
        ws = target.investigation.workspace
        return {
            "metric": list(ws.metric),
            "entity": list(ws.entity),
            "series": list(ws.series),
            "taxonomy": ws.taxonomy,
            "frequency": ws.frequency,
            "head": ws.head,
            "tail": ws.tail,
            "_target": {
                "project_id": target.project_id,
                "investigation_id": target.investigation_id,
            },
        }

    result = run_operation("workspace.show", _run, inputs={}, app_dir=app_dir)
    # Lift target ids onto the result when successful.
    if result.status == "success" and isinstance(result.data, dict):
        target_meta = result.data.pop("_target", None)
        if target_meta:
            result.project_id = target_meta["project_id"]
            result.investigation_id = target_meta["investigation_id"]
    return result


def list_saved_queries(
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    app_dir=None,
):
    def _run():
        target = resolve_target(
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            require=True,
        )
        assert target is not None
        rows = [
            {
                "id": q.id,
                "name": q.name,
                "in_report": q.in_report,
                "metric": list(q.metric),
                "entity": list(q.entity),
                "series": list(q.series),
            }
            for q in sorted(
                target.investigation.queries.values(), key=lambda x: x.id
            )
        ]
        return {
            "queries": rows,
            "_target": {
                "project_id": target.project_id,
                "investigation_id": target.investigation_id,
            },
        }

    result = run_operation("query.list", _run, inputs={}, app_dir=app_dir)
    if result.status == "success" and isinstance(result.data, dict):
        target_meta = result.data.pop("_target", None)
        if target_meta:
            result.project_id = target_meta["project_id"]
            result.investigation_id = target_meta["investigation_id"]
        result.data = result.data.get("queries", result.data)
    return result


def actions_log(
    *,
    project: Optional[str] = None,
    investigation: Optional[str] = None,
    operation: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    app_dir=None,
):
    """List recent action-log events (does not itself write a nested event)."""
    from .logstore import ActionLogStore
    from .result import OperationResult
    from .runner import get_store

    target = resolve_target(
        project=project,
        investigation=investigation,
        app_dir=app_dir,
        require=False,
    )
    store = get_store(app_dir=app_dir)
    events = store.list_events(
        project_id=target.project_id if target else project,
        investigation_id=target.investigation_id if target else investigation,
        operation=operation,
        status=status,
        limit=limit,
        offset=offset,
    )
    return OperationResult(
        operation="actions.log",
        status="success",
        event_id="evt_log_view",
        data={"events": events, "count": len(events)},
        inputs={
            "project": project,
            "investigation": investigation,
            "operation": operation,
            "status": status,
            "limit": limit,
            "offset": offset,
        },
        project_id=target.project_id if target else None,
        investigation_id=target.investigation_id if target else None,
    )


def inspect_event(event_id: str, *, app_dir=None):
    """Load a historical event and its artifact (no Data API call)."""
    from .result import OperationResult
    from .runner import get_store

    store = get_store(app_dir=app_dir)
    try:
        event = store.get_event(event_id)
    except KeyError as exc:
        return OperationResult(
            operation="actions.inspect",
            status="error",
            event_id="evt_inspect",
            error=str(exc),
            inputs={"event_id": event_id},
        )
    return OperationResult(
        operation="actions.inspect",
        status="success",
        event_id="evt_inspect",
        data=event,
        inputs={"event_id": event_id},
        project_id=event.get("project_id"),
        investigation_id=event.get("investigation_id"),
        artifact_id=event.get("artifact_id"),
        cache_hit=False,
    )


# Back-compat alias used by early tests / callers.
actions_log_stub = actions_log
