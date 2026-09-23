import sys
import click
from typing import List, Union, Optional

from .client import ApiKeyNotSetError, JSTDataClient, InvalidApiKeyError
from .utils import common_params, common_search_params, format_and_print

client = JSTDataClient()

def resolve_id(value: str, search_func) -> str:
    """
    Helper to resolve a potential ID or search for it.
    Emphasizes 'Intent over IDs'.
    """
    # Simple heuristic: if it looks like an ID (no spaces, all lowercase/dashes),
    # we might still want to check if it's valid, but for now let's just use search
    # if it's not a perfect match.
    # In a real scenario, we might try a direct lookup first.
    try:
        results = search_func(value, limit=1)
        if results:
            return results[0].id
    except Exception:
        pass
    return value

@click.group()
def cli():
    """
    Jefferson Street CLI - A Research OS for Financial Data.
    """

@cli.command()
@click.option("--api-key", prompt="Enter your Jefferson Street API Key", hide_input=True, help="Your API Key")
def login(api_key):
    """
    Authenticate with the Jefferson Street API.
    """
    click.echo("Validating API key...")
    try:
        if client.validate_key(api_key):
            click.secho("Success! Authenticated.", fg="green")
            client._cfg.write(api_key=api_key)
            from .client import CONFIG_FILE
            click.echo(f"Configuration saved to {CONFIG_FILE.absolute()}")
        else:
            click.secho("Error: Invalid API key.", fg="red", err=True)
            sys.exit(1)
    except Exception as e:
        click.secho(f"Error during validation: {e}", fg="red", err=True)
        sys.exit(1)

@cli.group()
def config():
    """
    Manage CLI configuration.
    """
    pass

@config.command("show")
@click.option("--verbose", "-v", is_flag=True, help="Show advanced configuration like base URL")
def config_show(verbose):
    """
    Display current configuration.
    """
    if verbose:
        click.echo(f"Base URL: {client.base_url}")
    
    try:
        key = client.api_key
        masked_key = f"{key[:4]}...{key[-4:]}"
        click.echo(f"API Key: {masked_key}")
    except ApiKeyNotSetError:
        click.echo("API Key: Not set")

@config.command("set")
@click.argument("key", type=click.Choice(["api_key", "base_url"]))
@click.argument("value")
def config_set(key, value):
    """
    Update a configuration value. Valid keys are "api_key" and "base_url".
    """
    client._cfg.write(**{key: value})
    click.echo(f"Set {key} to {value}")

# --- Metric Commands ---

@cli.group()
def metric():
    """Commands for interacting with Metrics (themes)."""

@metric.command("ls")
@common_params
def list_metrics(limit, offset, format):
    """List all available metrics."""
    results = client.list_metrics(limit=limit, offset=offset)
    format_and_print(results, format)

@metric.command("show")
@click.argument("id")
@click.option("--format", default="pretty")
def show_metric(id, format):
    """Show details for a specific metric."""
    results = client.get_metric(id)
    format_and_print(results, format)

@metric.command("search")
@click.argument("query", required=False, default=None)
@click.option("--taxonomy", help="Restrict search to metrics with series in a taxonomy")
@click.option(
    "--entity",
    multiple=True,
    help="Restrict to metrics associated with these entities. Repeatable.",
)
@click.option(
    "--mode",
    type=click.Choice(["union", "intersect"]),
    default="union",
    help="How to combine multiple --entity values",
)
@common_search_params
def search_metrics(query, taxonomy, entity, mode, limit, offset, format):
    """Search for metrics by intent. Omit QUERY to list the matching set."""
    results = client.search_metrics(
        query,
        entity=list(entity) or None,
        taxonomy=taxonomy,
        mode=mode,
        limit=limit,
        offset=offset,
    )
    format_and_print(results, format)

@metric.command("series")
@click.argument("id")
@common_params
def metric_series(id, limit, offset, format):
    """List all series associated with a metric."""
    results = client.get_metric_series(id, limit=limit, offset=offset)
    format_and_print(results, format)

# --- Entity Commands ---

@cli.group()
def entity():
    """Commands for interacting with Entities (contexts)."""

@entity.command("show")
@click.argument("id")
@click.option("--format", default="pretty")
def show_entity(id, format):
    """Show details for a specific entity."""
    results = client.get_entity(id)
    format_and_print(results, format)

@entity.command("search")
@click.argument("query", required=False, default=None)
@click.option("--taxonomy", help="Restrict search to entities in a taxonomy")
@click.option(
    "--relation",
    multiple=True,
    help="Filter by relationship anchor (<relationship_type>:<to_entity_id>). Repeatable; OR'd.",
)
@click.option(
    "--metric",
    multiple=True,
    help="Restrict to entities associated with these metrics. Repeatable.",
)
@click.option(
    "--mode",
    type=click.Choice(["union", "intersect"]),
    default="union",
    help="How to combine multiple --metric values",
)
@common_search_params
def search_entities(query, taxonomy, relation, metric, mode, limit, offset, format):
    """Search for entities by intent. Omit QUERY to list the matching set."""
    results = client.search_entities(
        query,
        metric=list(metric) or None,
        taxonomy=taxonomy,
        relation=list(relation) or None,
        mode=mode,
        limit=limit,
        offset=offset,
    )
    format_and_print(results, format)

@entity.command("series")
@click.argument("id")
@common_params
def entity_series(id, limit, offset, format):
    """List all series associated with an entity."""
    results = client.get_entity_series(id, limit=limit, offset=offset)
    format_and_print(results, format)

@entity.command("relations")
@click.argument("id")
@common_params
def entity_relations(id, limit, offset, format):
    """Walk the entity graph."""
    results = client.get_entity_relations(id, limit=limit, offset=offset)
    format_and_print(results, format)

# --- Taxonomy Commands ---

@cli.group()
def taxonomy():
    """Commands for interacting with Taxonomies (membership catalogs)."""

@taxonomy.command("ls")
@common_params
def list_taxonomies(limit, offset, format):
    """List available taxonomies."""
    results = client.list_taxonomies(limit=limit, offset=offset)
    format_and_print(results, format)

@taxonomy.command("show")
@click.argument("id")
@click.option("--format", default="pretty")
def show_taxonomy(id, format):
    """Show details for a specific taxonomy."""
    results = client.get_taxonomy(id)
    format_and_print(results, format)

@taxonomy.command("entities")
@click.argument("id")
@common_params
def taxonomy_entities(id, limit, offset, format):
    """List entities that participate in a taxonomy."""
    results = client.get_taxonomy_entities(id, limit=limit, offset=offset)
    format_and_print(results, format)

@taxonomy.command("metrics")
@click.argument("id")
@common_params
def taxonomy_metrics(id, limit, offset, format):
    """List metrics with series on entities in a taxonomy."""
    results = client.get_taxonomy_metrics(id, limit=limit, offset=offset)
    format_and_print(results, format)

# --- Series Commands ---

@cli.group()
def series():
    """Commands for interacting with Series (data points)."""

@series.command("ls")
@common_params
def list_series(limit, offset, format):
    """List all available series."""
    results = client.list_series(limit=limit, offset=offset)
    format_and_print(results, format)

@series.command("show")
@click.argument("id")
@click.option("--format", default="pretty")
def show_series(id, format):
    """Show details for a specific series."""
    results = client.get_series(id)
    format_and_print(results, format)

@series.command("search")
@click.argument("query")
@common_search_params
def search_series(query, limit, offset, format):
    """Search for series by intent."""
    results = client.search_series(query, limit=limit, offset=offset)
    format_and_print(results, format)

@series.command("observations")
@click.argument("id")
@click.option("--start-date", help="Start date (YYYY-MM-DD)")
@click.option("--end-date", help="End date (YYYY-MM-DD)")
@click.option("--start-time", type=int, help="Start time (unix timestamp)")
@click.option("--end-time", type=int, help="End time (unix timestamp)")
@click.option(
    "--order-by",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    help="Sort observations by timestamp",
)
@common_params
def series_observations(
    id, start_date, end_date, start_time, end_time, order_by, limit, offset, format
):
    """Paginated history for one series."""
    results = client.get_series_observations(
        id,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    format_and_print(results, format)

# --- Query Command ---

@cli.command()
@click.option("--metric", multiple=True, help="Metric ID(s) or keywords")
@click.option("--entity", multiple=True, help="Entity ID(s) or keywords")
@click.option("--series", multiple=True, help="Series ID(s) or keywords")
@click.option("--frequency", type=click.Choice(["Annual", "Quarterly", "Monthly", "Daily", "Intraday"]))
@click.option(
    "--taxonomy",
    help="Restrict to series whose entities have an identity relation to this taxonomy",
)
@click.option("--head", type=int, help="Earliest N observations per series")
@click.option("--tail", type=int, help="Latest N observations per series (default 20)")
@click.option("--as-of", "as_of", help="Timezone-aware ISO-8601 cutoff (release_timestamp)")
@click.option(
    "--sort-by",
    "sort_by",
    type=click.Choice(["id", "value"]),
    default="id",
    help="Order series by id (default) or by last value in the window (desc)",
)
@click.option("--fuzzy", is_flag=True, default=True, help="Try to resolve keywords to IDs automatically")
@click.option(
    "--limit",
    default=50,
    help="Maximum number of series to return (default: 50, max: 50)",
)
@click.option("--offset", default=0, help="Number of series to skip (default: 0)")
@click.option(
    "--format",
    default="pretty",
    help="Output format. Valid formats are: json, csv, pretty.",
)
def query(
    metric,
    entity,
    series,
    frequency,
    taxonomy,
    head,
    tail,
    as_of,
    sort_by,
    fuzzy,
    limit,
    offset,
    format,
):
    """
    Bounded cross-sectional query. Mix metrics, entities, and series.

    Uses head/tail per series (not a date window). For deep history of one
    series, use `jst series observations`. Use --sort-by value with
    --taxonomy to rank a population (e.g. country GDP).
    """
    m_ids = list(metric)
    e_ids = list(entity)
    s_ids = list(series)

    if fuzzy:
        m_ids = [resolve_id(m, client.search_metrics) for m in m_ids]
        e_ids = [resolve_id(e, client.search_entities) for e in e_ids]
        s_ids = [resolve_id(s, client.search_series) for s in s_ids]

    results = client.query(
        metric=m_ids or None,
        entity=e_ids or None,
        series=s_ids or None,
        frequency=frequency,
        taxonomy=taxonomy,
        head=head,
        tail=tail,
        as_of=as_of,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    )
    
    format_and_print(results, format)

@cli.command("tutorial")
@click.option(
    "--session",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Preload a session JSON into the pipeline",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=str),
    help="Default path for export session writes",
)
def tutorial_cmd(session: str | None, output: str | None) -> None:
    """Run the built-in interactive tutorial (alias for workflow run tutorial)."""
    from .workflows import run_tutorial

    run_tutorial(client, session_path=session, output_path=output)


@cli.command("agent-guide")
def agent_guide_cmd() -> None:
    """Print a markdown bootstrap guide for agents (version-tied)."""
    from .agent_guide import render_agent_guide

    click.echo(render_agent_guide(cli), nl=False)


@cli.command("steps")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable catalog")
def steps_cmd(as_json: bool) -> None:
    """List available investigation steps."""
    import json as json_lib

    from .workflows import list_steps

    specs = list_steps()
    if as_json:
        click.echo(json_lib.dumps([s.to_dict() for s in specs], indent=2))
        return
    if not specs:
        click.echo("No steps registered.")
        return
    for spec in specs:
        click.echo(f"{spec.id:20} {spec.name} — {spec.description}")


@cli.command("step")
@click.argument("step_id")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable step metadata")
def step_cmd(step_id: str, as_json: bool) -> None:
    """Describe a step: arguments, requirements, example."""
    import json as json_lib

    from .workflows import format_step_help, get_step

    try:
        spec = get_step(step_id)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    if as_json:
        click.echo(json_lib.dumps(spec.to_dict(), indent=2))
        return
    click.echo(format_step_help(spec), nl=False)


@cli.command(
    "run",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
    },
)
@click.option(
    "--session",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Preload a session JSON into the pipeline",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=str),
    help="Default path for export session writes",
)
@click.pass_context
def run_cmd(ctx: click.Context, session: str | None, output: str | None) -> None:
    """Run one or more steps, daisy-chained with ':'

    \b
    jst run console
    jst run --session in.json console : console
    jst run company-selector --industry semiconductors : console
    """
    from .workflows import PipelineError, run_pipeline

    if not ctx.args:
        click.echo(ctx.get_help())
        raise SystemExit(2)
    try:
        run_pipeline(client, ctx.args, session_path=session, output_path=output)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except PipelineError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)


@cli.group("workflow")
def workflow_group() -> None:
    """Save and run named step pipelines."""


# Backward-compatible plural alias (same group object).
cli.add_command(workflow_group, "workflows")


@workflow_group.command("ls")
def workflow_ls() -> None:
    """List saved workflows."""
    from .workflows import (
        format_pipeline,
        is_bundled_workflow,
        list_saved_workflows,
    )

    workflows = list_saved_workflows()
    if not workflows:
        click.echo("No saved workflows.")
        return
    for wf in workflows:
        desc = wf.description or "-"
        tag = " (built-in)" if is_bundled_workflow(wf.id) else ""
        click.echo(
            f"{wf.id:24}{tag:11} {desc:40} {format_pipeline(wf)}"
        )


@workflow_group.command("rm")
@click.argument("workflow_id")
def workflow_rm(workflow_id: str) -> None:
    """Delete a saved workflow."""
    from .workflows import WorkflowStoreError, delete_workflow

    try:
        delete_workflow(workflow_id)
    except WorkflowStoreError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"Removed workflow {workflow_id!r}.")


@workflow_group.command(
    "create",
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "allow_interspersed_args": False,
    },
)
@click.option(
    "--id",
    "workflow_id",
    required=True,
    help="Slug id for the workflow (e.g. gdp-rank)",
)
@click.option(
    "--description",
    default="",
    help="Optional short description",
)
@click.pass_context
def workflow_create(
    ctx: click.Context, workflow_id: str, description: str
) -> None:
    """Save a step pipeline as a named workflow.

    \b
    jst workflow create --id gdp-rank -- console : rank --taxonomy country
    jst workflow create --id gdp --description "GDP leaders" -- console : rank
    """
    import sys as _sys

    from .workflows import (
        PipelineError,
        WorkflowStoreError,
        create_workflow_from_tokens,
        save_workflow,
        workflow_exists,
    )

    # Hard boundary: pipeline must follow '--'. Enforce when this process
    # looks like a real ``jst workflow create`` (CliRunner leaves sys.argv alone).
    try:
        create_at = _sys.argv.index("create")
        via_workflow = any(
            name in _sys.argv[:create_at] for name in ("workflow", "workflows")
        )
    except ValueError:
        via_workflow = False
    if via_workflow and "--" not in _sys.argv[create_at:]:
        click.echo(
            "Error: pass the pipeline after '--'.\n"
            "Example: jst workflow create --id gdp-rank -- console : rank",
            err=True,
        )
        sys.exit(2)
    if not ctx.args:
        click.echo(ctx.get_help())
        raise SystemExit(2)

    try:
        if workflow_exists(workflow_id):
            if not click.confirm(
                f"Workflow {workflow_id!r} already exists. Overwrite?",
                default=False,
            ):
                click.echo("Aborted.")
                return
        workflow = create_workflow_from_tokens(workflow_id, description, list(ctx.args))
        path = save_workflow(workflow)
    except WorkflowStoreError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except PipelineError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    click.echo(f"Saved workflow {workflow.id!r} → {path}")


@workflow_group.command("run")
@click.argument("workflow_id")
@click.option(
    "--session",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help="Preload a session JSON into the pipeline",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=str),
    help="Default path for export session writes",
)
def workflow_run(
    workflow_id: str, session: str | None, output: str | None
) -> None:
    """Run a saved workflow (validates before launching the UI).

    \b
    jst workflow run gdp-rank
    jst workflow run gdp-rank --session in.json
    """
    from .workflows import (
        PipelineError,
        TUTORIAL_WORKFLOW_ID,
        WorkflowStoreError,
        load_workflow,
        resolve_saved_workflow,
        run_resolved_pipeline,
        run_tutorial,
    )

    try:
        workflow = load_workflow(workflow_id)
        if workflow_id == TUTORIAL_WORKFLOW_ID:
            run_tutorial(client, session_path=session, output_path=output)
            return
        resolved = resolve_saved_workflow(workflow)
    except WorkflowStoreError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    except PipelineError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    run_resolved_pipeline(
        client, resolved, session_path=session, output_path=output
    )


@cli.group("investigation")
def investigation_group() -> None:
    """Create and open persistent investigations."""


@investigation_group.command("create")
@click.argument("investigation_id")
@click.option(
    "--title",
    default=None,
    help="Display title (defaults to the investigation id)",
)
def investigation_create(investigation_id: str, title: str | None) -> None:
    """Create an investigation, activate it, and open the TUI.

    \b
    jst investigation create housing-slowdown
    jst investigation create eu-defense --title "European defense constraints"
    """
    from .investigation import (
        InvestigationError,
        create_investigation,
        run_investigation_host,
    )

    try:
        inv = create_investigation(investigation_id, title=title)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(f"Created investigation {inv.id!r} ({inv.title})")
    run_investigation_host(client)


@investigation_group.command("load")
@click.argument("investigation_id")
def investigation_load(investigation_id: str) -> None:
    """Load an investigation, activate it, and open the TUI.

    \b
    jst investigation load tutorial
    jst investigation load housing-slowdown
    """
    from .investigation import (
        InvestigationError,
        open_investigation,
        run_investigation_host,
    )

    try:
        inv = open_investigation(investigation_id)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(f"Loaded investigation {inv.id!r} ({inv.title})")
    run_investigation_host(client)


@investigation_group.command("ls")
def investigation_ls() -> None:
    """List investigations on disk (active marked with *).

    \b
    jst investigation ls
    """
    from .investigation import (
        ensure_bootstrap,
        get_active,
        list_investigations,
        load_investigation,
    )
    from .investigation.store import investigations_dir

    ensure_bootstrap()
    try:
        active_id = get_active().investigation
    except Exception:
        active_id = None

    ids = list_investigations()
    if not ids:
        click.echo("No investigations.")
        return
    root = investigations_dir()
    for inv_id in ids:
        marker = "*" if inv_id == active_id else " "
        try:
            inv = load_investigation(inv_id, root=root)
            title = inv.title
        except Exception:
            title = "?"
        click.echo(f"{marker} {inv_id:<28} {title}")


@investigation_group.command("add")
@click.argument("action_type")
@click.option(
    "--args",
    "args_json",
    default=None,
    help='JSON object of step kwargs, e.g. \'{"taxonomy":"country"}\'',
)
@click.option(
    "--parent",
    "parent_id",
    type=int,
    default=None,
    help="Parent action id (default: active action)",
)
@click.option(
    "--description",
    default=None,
    help="Optional action description",
)
@click.option(
    "--activate",
    is_flag=True,
    default=False,
    help="Make the new action active after creating it",
)
def investigation_add(
    action_type: str,
    args_json: str | None,
    parent_id: int | None,
    description: str | None,
    activate: bool,
) -> None:
    """Add an action under the active investigation.

    \b
    jst investigation add console
    jst investigation add console --args '{"taxonomy":"country"}'
    jst investigation add discover --parent 0 --activate
    """
    from .investigation import (
        InvestigationError,
        add_action,
        parse_action_args_json,
        require_active_investigation,
    )

    try:
        inv, _ctx = require_active_investigation()
        args = parse_action_args_json(args_json)
        action = add_action(
            inv,
            action_type,
            parent_id=parent_id,
            args=args,
            description=description,
            activate=activate,
        )
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Added action {action.id} type={action.type} "
        f"parent={action.parent_id}"
        + (" (activated)" if activate else "")
    )


@investigation_group.command("rm")
@click.argument("action_id", type=int)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Required to remove an action with children, or the active action",
)
def investigation_rm(action_id: int, force: bool) -> None:
    """Remove an action (and descendants) from the active investigation.

    \b
    jst investigation rm 3
    jst investigation rm 1 --force
    """
    from .investigation import (
        InvestigationError,
        remove_action,
        require_active_investigation,
    )

    try:
        inv, _ctx = require_active_investigation()
        removed = remove_action(inv, action_id, force=force)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Removed action{'s' if len(removed) != 1 else ''} "
        + ", ".join(str(i) for i in removed)
    )


if __name__ == "__main__":
    try:
        cli()
    except ApiKeyNotSetError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        if sys.stdin.isatty():
            if click.confirm("Would you like to run 'jstdata login' now?"):
                # Use click.Context to invoke the login command
                ctx = cli.make_context("login", [])
                cli.invoke(ctx)
        else:
            sys.exit(1)
    except InvalidApiKeyError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        sys.exit(1)
