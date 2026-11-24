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


@metadata_group.command("series")
@click.argument("series_id")
@_refresh_option
@_target_options
@_format_option
def metadata_series_cmd(
    series_id: str,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.get_series_metadata(
        _client(),
        series_id,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@metadata_group.command("release")
@click.argument("release_id")
@_refresh_option
@_target_options
@_format_option
def metadata_release_cmd(
    release_id: str,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    result = disc.get_release_metadata(
        _client(),
        release_id,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.command("coverage")
@click.option("--metric", multiple=True)
@click.option("--entity", multiple=True)
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def coverage_cmd(
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Metric × entity coverage preview (no observations)."""
    result = disc.coverage_preview(
        _client(),
        metric=metric or None,
        entity=entity or None,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_group.group("workspace")
def workspace_group() -> None:
    """Stage and inspect research intent (no observations)."""


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


@workspace_group.command("add")
@click.option("--metric", multiple=True)
@click.option("--entity", multiple=True)
@click.option("--series", multiple=True)
@_target_options
@_format_option
def workspace_add_cmd(
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    series: tuple[str, ...],
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import workspace_ops as ws

    result = ws.workspace_add(
        metric=metric or None,
        entity=entity or None,
        series=series or None,
        project=project,
        investigation=investigation,
    )
    _echo_result(result, format=fmt)


@workspace_group.command("rm")
@click.argument("ids", nargs=-1, required=True)
@_target_options
@_format_option
def workspace_rm_cmd(
    ids: tuple[str, ...],
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import workspace_ops as ws

    result = ws.workspace_rm(
        ids,
        project=project,
        investigation=investigation,
    )
    _echo_result(result, format=fmt)


@actions_group.group("query")
def actions_query_group() -> None:
    """Exploratory and saved queries (logged)."""


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


@actions_query_group.command("show")
@click.argument("query_id", type=int)
@_target_options
@_format_option
def query_show_cmd(
    query_id: int,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import query_ops

    result = query_ops.show_query_op(
        query_id, project=project, investigation=investigation
    )
    _echo_result(result, format=fmt)


@actions_query_group.command("inspect")
@click.argument("query_id", type=int)
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_refresh_option
@_target_options
@_format_option
def query_inspect_cmd(
    query_id: int,
    limit: int,
    offset: int,
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import query_ops

    result = query_ops.inspect_query_op(
        _client(),
        query_id,
        limit=limit,
        offset=offset,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_query_group.command("run")
@click.option("--query", "query_id", type=int, default=None)
@click.option(
    "--spec",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    default=None,
    help="JSON query spec file",
)
@click.option("--metric", multiple=True)
@click.option("--entity", multiple=True)
@click.option("--series", multiple=True)
@click.option("--frequency", default=None)
@click.option("--taxonomy", default=None)
@click.option("--head", type=int, default=None)
@click.option("--tail", type=int, default=None)
@click.option("--as-of", "as_of", default=None)
@click.option("--sort-by", "sort_by", default=None)
@click.option("--order-by", "order_by", default=None)
@click.option(
    "--save/--no-save",
    default=False,
    help="Persist as exploratory query (in_report=false)",
)
@click.option("--name", default=None)
@_refresh_option
@_target_options
@_format_option
def query_run_cmd(
    query_id: Optional[int],
    spec: Optional[str],
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    series: tuple[str, ...],
    frequency: Optional[str],
    taxonomy: Optional[str],
    head: Optional[int],
    tail: Optional[int],
    as_of: Optional[str],
    sort_by: Optional[str],
    order_by: Optional[str],
    save: bool,
    name: Optional[str],
    refresh: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Execute a query; log records as an immutable artifact."""
    from pathlib import Path

    from .ops import query_ops

    result = query_ops.run_query_op(
        _client(),
        query_id=query_id,
        spec=Path(spec) if spec else None,
        metric=metric or None,
        entity=entity or None,
        series=series or None,
        frequency=frequency,
        taxonomy=taxonomy,
        head=head,
        tail=tail,
        as_of=as_of,
        sort_by=sort_by,
        order_by=order_by,
        save=save,
        name=name,
        project=project,
        investigation=investigation,
        use_cache=not refresh,
    )
    _echo_result(result, format=fmt)


@actions_query_group.command("add")
@click.option("--metric", multiple=True)
@click.option("--entity", multiple=True)
@click.option("--series", multiple=True)
@click.option("--frequency", default=None)
@click.option("--taxonomy", default=None)
@click.option("--head", type=int, default=None)
@click.option("--tail", type=int, default=None)
@click.option("--as-of", "as_of", default=None)
@click.option("--sort-by", "sort_by", default=None)
@click.option("--order-by", "order_by", default=None)
@click.option("--name", default=None)
@click.option("--annotations", default=None)
@click.option(
    "--in-report/--exploratory",
    default=False,
    help="Report membership (default exploratory)",
)
@click.option("--from-workspace", is_flag=True, default=False)
@_target_options
@_format_option
def query_add_cmd(
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    series: tuple[str, ...],
    frequency: Optional[str],
    taxonomy: Optional[str],
    head: Optional[int],
    tail: Optional[int],
    as_of: Optional[str],
    sort_by: Optional[str],
    order_by: Optional[str],
    name: Optional[str],
    annotations: Optional[str],
    in_report: bool,
    from_workspace: bool,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import query_ops

    result = query_ops.add_query_op(
        metric=metric or None,
        entity=entity or None,
        series=series or None,
        frequency=frequency,
        taxonomy=taxonomy,
        head=head,
        tail=tail,
        as_of=as_of,
        sort_by=sort_by,
        order_by=order_by,
        name=name,
        annotations=annotations,
        in_report=in_report,
        from_workspace=from_workspace,
        project=project,
        investigation=investigation,
    )
    _echo_result(result, format=fmt)


@actions_query_group.command("rm")
@click.argument("query_id", type=int)
@_target_options
@_format_option
def query_rm_cmd(
    query_id: int,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import query_ops

    result = query_ops.remove_query_op(
        query_id, project=project, investigation=investigation
    )
    _echo_result(result, format=fmt)


@actions_group.command("promote")
@click.argument("event_id", required=False, default=None)
@click.option(
    "--query",
    "query_id",
    type=int,
    default=None,
    help="Promote an existing saved query into the report",
)
@click.option("--name", default=None, help="Name when promoting a query-shaped event")
@_target_options
@_format_option
def promote_cmd(
    event_id: Optional[str],
    query_id: Optional[int],
    name: Optional[str],
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Promote an action-log event or saved query into the report."""
    from .ops import curation as cur

    if query_id is not None:
        result = cur.promote_query(
            query_id,
            project=project,
            investigation=investigation,
        )
    elif event_id:
        result = cur.promote_event(
            event_id,
            name=name,
            project=project,
            investigation=investigation,
        )
    else:
        click.echo(
            "Usage: jst actions promote <event-id> | --query <id>",
            err=True,
        )
        sys.exit(2)
    _echo_result(result, format=fmt)


@actions_group.command("copy")
@click.option("--project", required=True, help="Project id")
@click.option("--from", "source_id", required=True, help="Source investigation id")
@click.option("--to", "dest_id", required=True, help="Destination investigation id")
@click.option("--metric", multiple=True)
@click.option("--entity", multiple=True)
@click.option("--series", multiple=True)
@click.option("--query", "query_ids", multiple=True, type=int)
@_format_option
def copy_cmd(
    project: str,
    source_id: str,
    dest_id: str,
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    series: tuple[str, ...],
    query_ids: tuple[int, ...],
    fmt: str,
) -> None:
    """Copy workspace ids / queries across investigations (no API calls)."""
    from .ops import curation as cur

    result = cur.copy_across_investigations(
        project_id=project,
        source_id=source_id,
        dest_id=dest_id,
        metric=metric or None,
        entity=entity or None,
        series=series or None,
        query_ids=query_ids or None,
    )
    _echo_result(result, format=fmt)


@actions_group.group("feedback")
def feedback_group() -> None:
    """Local feedback outbox (queued until Workstream C ingest)."""


@feedback_group.command("submit")
@click.argument("message")
@click.option("--screen", default=None, help="Optional screen / view id")
@_target_options
@_format_option
def feedback_submit_cmd(
    message: str,
    screen: Optional[str],
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    from .ops import feedback as fb

    result = fb.submit_feedback(
        message,
        project_id=project,
        investigation_id=investigation,
        screen=screen,
    )
    _echo_result(result, format=fmt)


@feedback_group.command("ls")
@click.option("--status", default=None)
@click.option("--limit", default=50, show_default=True)
@click.option("--offset", default=0, show_default=True)
@_format_option
def feedback_ls_cmd(
    status: Optional[str], limit: int, offset: int, fmt: str
) -> None:
    from .ops import feedback as fb

    result = fb.list_feedback(status=status, limit=limit, offset=offset)
    _echo_result(result, format=fmt)


@feedback_group.command("inspect")
@click.argument("feedback_id")
@_format_option
def feedback_inspect_cmd(feedback_id: str, fmt: str) -> None:
    from .ops import feedback as fb

    result = fb.inspect_feedback(feedback_id)
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


@actions_group.command("rerun")
@click.argument("event_id")
@click.option(
    "--refresh/--no-refresh",
    "refresh",
    default=True,
    help="Bypass artifact cache when re-dispatching (default refresh)",
)
@_format_option
def rerun_cmd(event_id: str, refresh: bool, fmt: str) -> None:
    """Re-dispatch a historical discovery/metadata/query operation."""
    from .ops import rerun as rerun_mod
    from .ops.result import OperationError

    try:
        result = rerun_mod.rerun_event(
            event_id, refresh=refresh, client=_client()
        )
    except OperationError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    _echo_result(result, format=fmt)


@actions_group.command("annotate")
@click.option("--text", required=True, help="Annotation / commentary text")
@click.option("--query", "query_id", type=int, default=None)
@click.option("--series", default=None, help="Series id for notes_on")
@click.option("--from", "date_from", default=None, help="notes_on from date")
@click.option("--to", "date_to", default=None, help="notes_on to date")
@_target_options
@_format_option
def annotate_cmd(
    text: str,
    query_id: Optional[int],
    series: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Set investigation/query commentary or append a notes_on slice."""
    from .ops import workspace_ops as ws
    from .ops.result import OperationError

    try:
        result = ws.annotate_op(
            text,
            query_id=query_id,
            series=series,
            date_from=date_from,
            date_to=date_to,
            project=project,
            investigation=investigation,
        )
    except OperationError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    _echo_result(result, format=fmt)


@actions_group.command("state")
@click.option("--recent", default=5, show_default=True)
@_target_options
@_format_option
def state_cmd(
    recent: int,
    project: Optional[str],
    investigation: Optional[str],
    fmt: str,
) -> None:
    """Show investigation research state + recent action-log events."""
    from .ops import workspace_ops as ws

    result = ws.state_show(
        project=project,
        investigation=investigation,
        recent=recent,
    )
    _echo_result(result, format=fmt)
