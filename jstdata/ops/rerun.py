"""Rerun a historical action-log event against current inputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from ..client import JSTDataClient
from . import discovery as disc
from . import query_ops
from .result import OperationError, OperationResult
from .runner import get_store


def _client_factory(client: Optional[JSTDataClient]) -> JSTDataClient:
    if client is not None:
        return client
    from ..cli import client as default_client

    return default_client


def rerun_event(
    event_id: str,
    *,
    refresh: bool = True,
    client: Optional[JSTDataClient] = None,
    app_dir: Optional[Path] = None,
) -> OperationResult:
    """Re-dispatch a prior operation using its recorded inputs.

    Mutators (workspace / curation / annotate / query.add|remove) are refused.
    """
    store = get_store(app_dir=app_dir)
    try:
        event = store.get_event(event_id)
    except KeyError as exc:
        raise OperationError(str(exc)) from exc

    operation = event.get("operation") or ""
    inputs = event.get("inputs") or {}
    if isinstance(inputs, str):
        import json

        try:
            inputs = json.loads(inputs)
        except json.JSONDecodeError:
            inputs = {}
    if not isinstance(inputs, dict):
        inputs = {}

    project = event.get("project_id")
    investigation = event.get("investigation_id")
    use_cache = not refresh
    api = _client_factory(client)

    dispatch: dict[str, Callable[[], OperationResult]] = {
        "search.entities": lambda: disc.search_entities(
            api,
            inputs.get("query"),
            taxonomy=inputs.get("taxonomy"),
            relation=inputs.get("relation"),
            relation_direction=inputs.get("relation_direction"),
            relation_taxonomy=inputs.get("relation_taxonomy"),
            limit=int(inputs.get("limit") or 20),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "search.metrics": lambda: disc.search_metrics(
            api,
            inputs.get("query"),
            taxonomy=inputs.get("taxonomy"),
            limit=int(inputs.get("limit") or 20),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "taxonomy.list": lambda: disc.list_taxonomies(
            api,
            limit=int(inputs.get("limit") or 50),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "taxonomy.entities": lambda: disc.taxonomy_entities(
            api,
            inputs["taxonomy_id"],
            limit=int(inputs.get("limit") or 50),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "entity.relationships": lambda: disc.entity_relationships(
            api,
            inputs["entity_id"],
            direction=inputs.get("direction"),
            relationship=inputs.get("relationship"),
            relation_taxonomy=inputs.get("relation_taxonomy"),
            taxonomy=inputs.get("taxonomy"),
            limit=int(inputs.get("limit") or 50),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "metadata.entity": lambda: disc.get_entity_metadata(
            api,
            inputs["entity_id"],
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "metadata.metric": lambda: disc.get_metric_metadata(
            api,
            inputs["metric_id"],
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "metadata.series": lambda: disc.get_series_metadata(
            api,
            inputs["series_id"],
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "metadata.release": lambda: disc.get_release_metadata(
            api,
            inputs["release_id"],
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "query.run": lambda: query_ops.run_query_op(
            api,
            query_id=inputs.get("query_id"),
            spec=inputs.get("spec"),
            metric=inputs.get("metric"),
            entity=inputs.get("entity"),
            series=inputs.get("series"),
            frequency=inputs.get("frequency"),
            taxonomy=inputs.get("taxonomy"),
            head=inputs.get("head"),
            tail=inputs.get("tail"),
            as_of=inputs.get("as_of"),
            sort_by=inputs.get("sort_by"),
            order_by=inputs.get("order_by"),
            save=False,
            name=inputs.get("name"),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "query.inspect": lambda: query_ops.inspect_query_op(
            api,
            int(inputs["query_id"]),
            limit=int(inputs.get("limit") or 50),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
        "coverage.preview": lambda: disc.coverage_preview(
            api,
            metric=inputs.get("metric"),
            entity=inputs.get("entity"),
            limit=int(inputs.get("limit") or 50),
            offset=int(inputs.get("offset") or 0),
            project=project,
            investigation=investigation,
            app_dir=app_dir,
            use_cache=use_cache,
        ),
    }

    fn = dispatch.get(operation)
    if fn is None:
        raise OperationError(
            f"Cannot rerun operation {operation!r}. "
            "Only idempotent discovery/metadata/query ops are supported."
        )
    try:
        return fn()
    except KeyError as exc:
        raise OperationError(
            f"Event {event_id!r} inputs missing required field: {exc}"
        ) from exc
