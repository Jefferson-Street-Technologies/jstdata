"""``jst actions`` — shared research operations CLI."""

from __future__ import annotations

import json
import sys
from typing import Optional

import click

from .ops import OperationResult
from .ops import discovery as disc


def _client():
    from .cli import client

    return client


def _echo_result(result: OperationResult, *, format: str = "json") -> None:
    payload = result.to_dict()
    if format == "json":
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(
            f"event={result.event_id} status={result.status} op={result.operation}"
        )
        if result.error:
            click.echo(f"error: {result.error}", err=True)
        elif result.data is not None:
            click.echo(json.dumps(result.data, indent=2, default=str))
    if result.status == "error":
        sys.exit(1)


def _target_options(fn):
    fn = click.option(
        "--investigation",
        default=None,
        help="Investigation id (agents should always pass explicitly)",
    )(fn)
    fn = click.option(
        "--project",
        default=None,
        help="Project id (agents should always pass explicitly)",
    )(fn)
    return fn


def _format_option(fn):
    return click.option(
        "--format",
        "fmt",
        type=click.Choice(["json", "pretty"]),
        default="json",
        help="Output format (default json for agents)",
    )(fn)


def _refresh_option(fn):
    """Bypass the local artifact cache and hit the Data API again."""
    return click.option(
        "--refresh/--no-refresh",
        "refresh",
        default=False,
        help=(
            "Skip the local action-log artifact cache and re-fetch from the "
            "Data API (also writes a fresh artifact)"
        ),
    )(fn)


@click.group(invoke_without_command=True)
@click.pass_context
def actions_group(ctx) -> None:
    """Logged research operations (shared by humans and agents).

    Prefer this over ``jst api`` / ``jst action`` / ``jst query``.
    Pass ``--project`` and ``--investigation`` explicitly when mutating state.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@actions_group.group("search")
def search_group() -> None:
    """Discover entities, metrics, and related universes."""


@search_group.command("entities")
@click.argument("query", required=False, default=None)
@click.option("--taxonomy", default=None, help="Membership taxonomy slug")
@click.option(
    "--relation",
    multiple=True,
    help="Relation filter type:anchor_id (repeatable, OR'd)",
)
@click.option(
    "--relation-direction",
    type=click.Choice(["outgoing", "incoming"]),
    default=None,
    help="Relative to candidate entity (default outgoing on server)",
)
@click.option(
    "--relation-taxonomy",
    default=None,
    help="Edge taxonomy slug (requires --relation)",
)
@click.option("--limit", default=20, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def search_entities_cmd(
    query: Optional[str],
    taxonomy: Optional[str],
    relation: tuple[str, ...],
    relation_direction: Optional[str],
    relation_taxonomy: Optional[str],
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Search entities (resolve IDs here — never invent them)."""
    result = disc.search_entities(
        _client(),
        query,
        taxonomy=taxonomy,
        relation=relation or None,
        relation_direction=relation_direction,
        relation_taxonomy=relation_taxonomy,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@search_group.command("metrics")
@click.argument("query", required=False, default=None)
@click.option("--taxonomy", default=None)
@click.option("--entity", multiple=True)
@click.option("--limit", default=20, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def search_metrics_cmd(
    query: Optional[str],
    taxonomy: Optional[str],
    entity: tuple[str, ...],
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Search metrics."""
    result = disc.search_metrics(
        _client(),
        query,
        taxonomy=taxonomy,
        entity=entity or None,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.group("taxonomy")
def taxonomy_group() -> None:
    """Browse taxonomies and their members."""


@taxonomy_group.command("ls")
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def taxonomy_ls_cmd(
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.list_taxonomies(
        _client(),
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@taxonomy_group.command("entities")
@click.argument("taxonomy_id")
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def taxonomy_entities_cmd(
    taxonomy_id: str,
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.taxonomy_entities(
        _client(),
        taxonomy_id,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.command("relationships")
@click.argument("entity_id")
@click.option(
    "--direction",
    type=click.Choice(["outgoing", "incoming"]),
    default=None,
)
@click.option(
    "--relationship",
    type=click.Choice(
        ["classified_as", "equivalent_to", "part_of", "has_security"]
    ),
    default=None,
)
@click.option("--relation-taxonomy", default=None)
@click.option(
    "--taxonomy",
    default=None,
    help="Membership taxonomy of the related entity",
)
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def relationships_cmd(
    entity_id: str,
    direction: Optional[str],
    relationship: Optional[str],
    relation_taxonomy: Optional[str],
    taxonomy: Optional[str],
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Retrieve entity relationships (direction + filters from Data API)."""
    result = disc.entity_relationships(
        _client(),
        entity_id,
        direction=direction,
        relationship=relationship,
        relation_taxonomy=relation_taxonomy,
        taxonomy=taxonomy,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.group("metadata")
def metadata_group() -> None:
    """Retrieve entity / metric metadata."""


@metadata_group.command("entity")
@click.argument("entity_id")
@_refresh_option
@_target_options
@_format_option
def metadata_entity_cmd(
    entity_id: str,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.get_entity_metadata(
        _client(),
        entity_id,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@metadata_group.command("metric")
@click.argument("metric_id")
@_refresh_option
@_target_options
@_format_option
def metadata_metric_cmd(
    metric_id: str,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.get_metric_metadata(
        _client(),
        metric_id,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.group("workspace")
def workspace_group() -> None:
    """Inspect staged research intent."""


@workspace_group.command("show")
@_target_options
@_format_option
def workspace_show_cmd(
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.workspace_show(
        project=project, investigation=investigation
    )
    _echo_result(result, format=fmt)


@actions_group.group("query")
def actions_query_group() -> None:
    """Saved / exploratory queries (more commands in later slices)."""


@actions_query_group.command("ls")
@_target_options
@_format_option
def query_ls_cmd(
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.list_saved_queries(
        project=project, investigation=investigation
    )
    _echo_result(result, format=fmt)


@actions_group.command("log")
@click.option("--operation", default=None, help="Filter by operation id")
@click.option("--status", default=None, help="Filter by status")
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_target_options
@_format_option
def log_cmd(
    operation: Optional[str],
    status: Optional[str],
    limit: int,
    offset: int,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """List recent action-log events from the local SQLite store."""
    result = disc.actions_log(
        project=project,
        investigation=investigation,
        operation=operation,
        status=status,
        limit=limit,
        offset=offset,
    )
    _echo_result(result, format=fmt)


@actions_group.command("inspect")
@click.argument("event_id")
@_format_option
def inspect_cmd(event_id: str, fmt: str) -> None:
    """Inspect a historical event and its artifact (no Data API call)."""
    result = disc.inspect_event(event_id)
    _echo_result(result, format=fmt)
