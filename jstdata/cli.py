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

# --- Low-level API (agent / power-user) ---

@cli.group()
def api():
    """Direct API access. Prefer investigations for research; use api for resolution and queries."""
    pass

# --- Metric Commands ---

@api.group()
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

@api.group()
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

@api.group()
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

@api.group()
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

@api.command()
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
    series, use `jst api series observations`. Use --sort-by value with
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

@cli.command("agent-guide")
def agent_guide_cmd() -> None:
    """Print a markdown bootstrap guide for agents (version-tied)."""
    from .agent_guide import render_agent_guide

    click.echo(render_agent_guide(cli), nl=False)


@cli.group("action")
def action_group() -> None:
    """Catalog and mutate investigation actions."""


@action_group.command("ls")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable catalog")
def action_ls(as_json: bool) -> None:
    """List available action types.

    \b
    jst action ls
    jst action ls --json
    """
    import json as json_lib

    from .actions import list_actions

    specs = list_actions()
    if as_json:
        click.echo(json_lib.dumps([s.to_dict() for s in specs], indent=2))
        return
    if not specs:
        click.echo("No actions registered.")
        return
    for spec in specs:
        click.echo(f"{spec.id:20} {spec.name} — {spec.description}")


@action_group.command("show")
@click.argument("action_id")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable action metadata")
def action_show(action_id: str, as_json: bool) -> None:
    """Describe an action type: seed args, keybindings, example.

    \b
    jst action show console
    jst action show rank --json
    """
    import json as json_lib

    from .actions import format_action_help, get_action

    try:
        spec = get_action(action_id)
    except KeyError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    if as_json:
        click.echo(json_lib.dumps(spec.to_dict(), indent=2))
        return
    click.echo(format_action_help(spec), nl=False)


@action_group.command("add")
@click.argument("action_type")
@click.option(
    "--args",
    "args_json",
    default=None,
    help='JSON object of action seed args, e.g. \'{"taxonomy":"country"}\'',
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
def action_add(
    action_type: str,
    args_json: str | None,
    parent_id: int | None,
    description: str | None,
    activate: bool,
) -> None:
    """Add an action under the active investigation.

    \b
    jst action add console
    jst action add console --args '{"taxonomy":"country"}'
    jst action add discover --parent 0 --activate
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


@action_group.command("rm")
@click.argument("action_id", type=int)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Required to remove an action with children, or the active action",
)
def action_rm(action_id: int, force: bool) -> None:
    """Remove an action (and descendants) from the active investigation.

    \b
    jst action rm 3
    jst action rm 1 --force
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


@cli.command("run")
def run_cmd() -> None:
    """Open the TUI for the active investigation / workspace / action.

    \b
    jst run
    """
    from .investigation import (
        InvestigationError,
        require_active_investigation,
        run_investigation_host,
    )

    try:
        inv, ctx = require_active_investigation()
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Running {inv.id!r} ({inv.title})  "
        f"workspace={ctx.workspace}  action={ctx.action}"
    )
    run_investigation_host(client)


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
