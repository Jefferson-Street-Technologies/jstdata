import sys
import click
import requests
from typing import List, Union, Optional

from .add_options import apply_click_add_options
from .cli_actions import actions_group
from .client import ApiKeyNotSetError, JSTDataClient, InvalidApiKeyError
from .utils import common_params, common_search_params, format_and_print

client = JSTDataClient()


class _RetiredGroup(click.Group):
    """Click group that always exits with a migration pointer."""

    def __init__(self, *args, retirement_message: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.retirement_message = retirement_message

    def get_command(self, ctx, cmd_name):
        click.echo(self.retirement_message, err=True)
        ctx.exit(2)

    def list_commands(self, ctx):
        return []

    def invoke(self, ctx):
        click.echo(self.retirement_message, err=True)
        ctx.exit(2)


def resolve_id(value: str, search_func, get_func=None) -> str:
    """
    Resolve a CLI argument to a resource id.

    An exact id wins: if ``get_func`` finds ``value``, its id is returned.
    Only a 404 means "not an id"; other lookup errors propagate rather than
    silently swapping in a search hit. Otherwise ``value`` is treated as
    keywords and the top search hit is used. If neither matches, ``value`` is
    passed through as-is.
    """
    if get_func is not None:
        try:
            return get_func(value).id
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 404:
                raise
        except KeyError:
            # Values like "", "." or "/" normalize to the list endpoint, which
            # returns no "id": not a resource.
            pass
    try:
        results = search_func(value, limit=1)
        if results:
            return results[0].id
    except Exception:
        pass
    return value

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """
    Jefferson Street CLI - A Research OS for Financial Data.

    Bare `jst` resumes the active investigation (creates Untitled if none).
    """
    if ctx.invoked_subcommand is None:
        _resume_investigation()


cli.add_command(actions_group, "actions")


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
        click.echo(f"Site URL: {_site_url()}")
    
    try:
        key = client.api_key
        masked_key = f"{key[:4]}...{key[-4:]}"
        click.echo(f"API Key: {masked_key}")
    except ApiKeyNotSetError:
        click.echo("API Key: Not set")

@config.command("set")
@click.argument("key", type=click.Choice(["api_key", "base_url", "site_url"]))
@click.argument("value")
def config_set(key, value):
    """
    Update a configuration value. Valid keys are "api_key", "base_url" and
    "site_url" (the website `jst publish` and `jst clone` use).
    """
    if key == "site_url":
        from .publish import PublishError, check_site_url

        try:
            value = check_site_url(value)
        except PublishError as e:
            click.secho(f"Error: {e}", fg="red", err=True)
            sys.exit(2)
    if not value:
        click.secho(f"Error: {key} can't be empty.", fg="red", err=True)
        sys.exit(2)
    client._cfg.write(**{key: value})
    click.echo(f"Set {key} to {value}")

# --- Low-level API (legacy; prefer ``jst actions``) ---

@cli.group()
@click.pass_context
def api(ctx):
    """Direct API access (deprecated). Prefer ``jst actions`` for research.

    Still available for ad-hoc resolution; agents should migrate to
    ``jst actions search`` / ``metadata`` / ``relationships``.
    """
    if ctx.invoked_subcommand is not None:
        click.echo(
            "Note: `jst api` is deprecated; prefer `jst actions …`.",
            err=True,
        )

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

@metric.command("taxonomies")
@click.argument("id")
@common_params
def metric_taxonomies(id, limit, offset, format):
    """List taxonomic coverage for a metric."""
    results = client.get_metric_taxonomies(id, limit=limit, offset=offset)
    format_and_print(results, format)

# --- Release Commands ---

@api.group()
def release():
    """Commands for releases / filings (observation provenance)."""

@release.command("ls")
@click.option(
    "--start-date",
    help="Inclusive start of the release-date window (YYYY-MM-DD).",
)
@click.option(
    "--end-date",
    help="Inclusive end of the release-date window (YYYY-MM-DD).",
)
@click.option(
    "--sort-order",
    type=click.Choice(["asc", "desc"]),
    default="desc",
    show_default=True,
    help="Sort by release date.",
)
@common_params
def list_releases(limit, offset, format, start_date, end_date, sort_order):
    """List releases, newest first by default."""
    results = client.list_releases(
        limit=limit,
        offset=offset,
        sort_order=sort_order,
        start_date=start_date,
        end_date=end_date,
    )
    format_and_print(results, format)

@release.command("show")
@click.argument("id")
@click.option("--format", default="pretty")
def show_release(id, format):
    """Show a release / filing by id."""
    results = client.get_release(id)
    format_and_print(results, format)

# --- Preview Command ---

@api.command("preview")
@click.option("--metric", multiple=True, required=True, help="Metric ID(s). Repeatable.")
@click.option("--entity", multiple=True, required=True, help="Entity ID(s). Repeatable.")
@common_params
def preview_coverage(metric, entity, limit, offset, format):
    """Cheap metric×entity coverage (pairs with series only). Cache results."""
    results = client.preview(
        metric=list(metric),
        entity=list(entity),
        limit=limit,
        offset=offset,
    )
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
@click.option("--taxonomy", help="Membership taxonomy slug (not a relationship filter)")
@click.option(
    "--relation",
    multiple=True,
    help="Filter by relationship anchor (<relationship_type>:<to_entity_id>). Repeatable; OR'd.",
)
@click.option(
    "--relation-direction",
    type=click.Choice(["outgoing", "incoming"]),
    default=None,
    help="Direction relative to the candidate (default outgoing)",
)
@click.option(
    "--relation-taxonomy",
    default=None,
    help="Edge taxonomy slug (requires --relation)",
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
def search_entities(
    query,
    taxonomy,
    relation,
    relation_direction,
    relation_taxonomy,
    metric,
    mode,
    limit,
    offset,
    format,
):
    """Search for entities by intent. Omit QUERY to list the matching set."""
    results = client.search_entities(
        query,
        metric=list(metric) or None,
        taxonomy=taxonomy,
        relation=list(relation) or None,
        mode=mode,
        limit=limit,
        offset=offset,
        relation_direction=relation_direction,
        relation_taxonomy=relation_taxonomy,
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
@click.option(
    "--direction",
    type=click.Choice(["outgoing", "incoming"]),
    default=None,
    help="Relative to the path entity (default outgoing)",
)
@click.option(
    "--relationship",
    type=click.Choice(
        ["classified_as", "equivalent_to", "part_of", "has_security"]
    ),
    default=None,
    help="Edge type (not identity)",
)
@click.option(
    "--relation-taxonomy",
    default=None,
    help="Edge taxonomy slug",
)
@click.option(
    "--taxonomy",
    default=None,
    help="Membership taxonomy of the related entity",
)
@common_params
def entity_relations(
    id,
    direction,
    relationship,
    relation_taxonomy,
    taxonomy,
    limit,
    offset,
    format,
):
    """Walk the entity graph (prefer ``jst actions relationships``)."""
    results = client.get_entity_relations(
        id,
        limit=limit,
        offset=offset,
        direction=direction,
        relationship=relationship,
        relation_taxonomy=relation_taxonomy,
        taxonomy=taxonomy,
    )
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
    "--release-id",
    "release_id",
    multiple=True,
    help="Filter to observations from these release ids. Repeatable.",
)
@click.option(
    "--order-by",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    help="Sort observations by timestamp",
)
@common_params
def series_observations(
    id,
    start_date,
    end_date,
    start_time,
    end_time,
    release_id,
    order_by,
    limit,
    offset,
    format,
):
    """Paginated history for one series."""
    results = client.get_series_observations(
        id,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        release_id=list(release_id) or None,
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
@click.option(
    "--release-id",
    "release_id",
    multiple=True,
    help="Filter observations to these release ids. Repeatable.",
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
@click.option(
    "--fuzzy/--no-fuzzy",
    default=True,
    help="Resolve keywords that are not exact IDs via search (default: on)",
)
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
    release_id,
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
        m_ids = [resolve_id(m, client.search_metrics, client.get_metric) for m in m_ids]
        e_ids = [resolve_id(e, client.search_entities, client.get_entity) for e in e_ids]
        s_ids = [resolve_id(s, client.search_series, client.get_series) for s in s_ids]

    results = client.query(
        metric=m_ids or None,
        entity=e_ids or None,
        series=s_ids or None,
        frequency=frequency,
        taxonomy=taxonomy,
        release_id=list(release_id) or None,
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


@cli.group("agent")
def agent_group() -> None:
    """Bootstrap agents: operating guide and skill install."""


@agent_group.command("guide")
def agent_guide_nested() -> None:
    """Print a markdown bootstrap guide for agents (version-tied)."""
    from .agent_guide import render_agent_guide

    click.echo(render_agent_guide(cli), nl=False)


@agent_group.command("install")
@click.argument(
    "target",
    type=click.Choice(["codex", "claude", "cursor", "all"]),
    default="all",
    required=False,
)
def agent_install_cmd(target: str) -> None:
    """Install the jst skill into user-level agent skill directories.

    \b
    jst agent install
    jst agent install cursor
    jst agent install all
    """
    from .agent.install import install_skill

    paths = install_skill(target)
    click.echo("Installed jst skill:")
    for path in paths:
        click.echo(f"  {path}")
    click.echo()
    click.echo("If the agent does not pick it up automatically, say:")
    click.echo("  Use the jst skill. Then run `jst agent-guide` and follow it.")


@cli.group(
    "action",
    cls=_RetiredGroup,
    retirement_message=(
        "`jst action` is retired.\n"
        "Use `jst actions …` for research operations "
        "(search, metadata, relationships, workspace, query, log).\n"
        "TUI view-tree edits happen inside `jst open` for now."
    ),
)
def action_group() -> None:
    """Retired — see ``jst actions``."""


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
@apply_click_add_options
def action_add(
    action_type: str,
    args_json: str | None,
    parent_id: int | None,
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


def _resume_investigation() -> None:
    """Bare `jst`: resume `.active.yaml`, or create Untitled if none."""
    from .investigation import (
        InvestigationError,
        get_active,
        resume_or_create_investigation,
        run_investigation_host,
    )

    try:
        inv = resume_or_create_investigation()
        ctx = get_active()
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Opening {inv.id!r} ({inv.title})  action={ctx.action}"
    )
    run_investigation_host(client)


@cli.command("new")
@click.option(
    "--id",
    "investigation_id",
    default=None,
    help="Slug (generated as inv-<unix> if omitted)",
)
@click.option("--title", default=None, help="Display title (defaults to Untitled)")
@click.option("--notes", default=None, help="Freeform notes (for agents / later you)")
def new_cmd(
    investigation_id: str | None, title: str | None, notes: str | None
) -> None:
    """Create an investigation, activate it, and open the TUI.

    Id and title are optional.

    \b
    jst new
    jst new --title "European defense constraints"
    jst new --id housing-slowdown --title Housing
    """
    from .investigation import (
        InvestigationError,
        create_investigation,
        run_investigation_host,
    )

    try:
        inv = create_investigation(investigation_id, title=title, notes=notes)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(f"Created investigation {inv.id!r} ({inv.title})")
    run_investigation_host(client)


@cli.command("open")
@click.argument("investigation_id")
def open_cmd(investigation_id: str) -> None:
    """Open an investigation, activate it, and launch the TUI.

    \b
    jst open tutorial
    jst open plain-bagel
    jst open housing-slowdown
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
    click.echo(f"Opened investigation {inv.id!r} ({inv.title})")
    run_investigation_host(client)


def _site_url() -> str:
    from .publish import configured_site_url

    return configured_site_url(client)


@cli.command("publish")
@click.argument("investigation_id", required=False, default=None)
@click.option("--name", "publisher_name", help="Name shown as the publisher (default: from your account email)")
@click.option("--yes", "-y", is_flag=True, help="Publish as-is: no draft, no confirmation (for scripts)")
@click.option("--no-edit", is_flag=True, help="Skip the draft and publish the text already saved")
@click.option("--update", "mode", flag_value="update", help="Update the text on this investigation's last link")
@click.option("--new", "mode", flag_value="new", help="Publish to a new link even if it was published before")
def publish_cmd(
    investigation_id: str | None,
    publisher_name: str | None,
    yes: bool,
    no_edit: bool,
    mode: str | None,
) -> None:
    """Publish an investigation as a shareable web page.

    Defaults to the active investigation. First a draft of the page opens:
    title, description, an overview, and a note under each query's charts,
    all in one Markdown document. Fill it in and press Ctrl+S; the text is
    saved to the investigation and published with it.

    The website runs the queries with your API key and returns a link. Anyone
    with the link can see the queries, text and results.

    If the investigation was published before, you can update that page's
    text in place (same link) or publish a new link. Updating keeps the
    results; if you changed the queries, publish a new link instead.

    \b
    jst publish
    jst publish plain-bagel --update
    jst publish plain-bagel --new --yes
    """
    from .investigation import InvestigationError, get_investigation
    from .investigation.active import try_load_active
    from .publish import (
        PublishError,
        QueriesChangedError,
        last_publication,
        publish_investigation,
        record_publication,
        update_publication,
    )
    from .publish_sync import text_of

    try:
        if investigation_id is None:
            ctx = try_load_active()
            if ctx is None:
                click.echo("Error: no active investigation. Name one: jst publish <id>", err=True)
                sys.exit(2)
            investigation_id = ctx.investigation
        inv = get_investigation(investigation_id)
        api_key = client.api_key
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    except ApiKeyNotSetError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    if not inv.queries:
        click.echo(f"Error: {inv.id!r} has no queries to publish. Add one with `jst query add`.", err=True)
        sys.exit(2)

    previous = last_publication(inv.id)
    if mode == "update" and previous is None:
        click.echo(f"Error: {inv.id!r} hasn't been published from this computer yet. Publish it first.", err=True)
        sys.exit(2)

    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    # Pull text edited on the page since the last publish, so an update
    # doesn't overwrite it (JEF-166).
    web_versions = None
    if previous is not None:
        from .investigation import persist_investigation
        from .publish_sync import label, pull_for_publish

        try:
            pull = pull_for_publish(inv, previous)
        except PublishError as e:
            click.secho(f"Couldn't check {previous['url']} for edits made on the page: {e}", fg="yellow", err=True)
            # Updating without checking could overwrite edits made on the page.
            if mode == "update" and (yes or not interactive):
                click.echo("Not updating. Try again, or publish a new link with --new.", err=True)
                sys.exit(1)
            if mode == "update" and not click.confirm(
                "Update anyway? Edits made on the page since your last publish would be replaced.",
                default=False,
            ):
                click.echo("Not updated.")
                return
        else:
            if pull.pulled:
                inv = pull.investigation
                persist_investigation(inv)
                click.echo("Pulled edits made on the page: " + ", ".join(label(k) for k in pull.pulled) + ".")
            if pull.conflicts:
                web_versions = pull.conflicts
                fields = ", ".join(label(k) for k in pull.conflicts)
                click.secho(
                    f"Edited both here and on the page: {fields}. Yours is kept; "
                    "the page's version is shown in the draft to merge.",
                    fg="yellow",
                )
                if mode == "update" and (yes or no_edit or not interactive):
                    click.echo(
                        "Not updating, so the page's edits aren't overwritten. Run `jst publish` "
                        "without --yes/--no-edit to merge them in the draft.",
                        err=True,
                    )
                    sys.exit(1)

    # The draft needs a terminal to edit in; scripts and agents skip it.
    if not (yes or no_edit) and interactive:
        from .investigation import persist_investigation
        from .investigation.publish_screens import edit_publish_draft

        edited = edit_publish_draft(inv, web_versions)
        if edited is None:
            click.echo("Not published.")
            return
        inv, changes = edited
        if changes:
            persist_investigation(inv)
            click.echo(f"Saved to {inv.id}: {changes.summary()}.")

    # Update the existing link, or publish a new one?
    if mode is None:
        mode = "new"
        if previous is not None and not yes:
            click.echo(f"{inv.id!r} was published at {previous['url']}")
            choice = click.prompt(
                "Update that page's text (u), or publish a new link (n)?",
                type=click.Choice(["u", "n"]),
                default="u",
            )
            mode = "update" if choice == "u" else "new"

    queries = sorted(inv.queries.values(), key=lambda q: q.id)
    site = _site_url()

    if mode == "update":
        assert previous is not None
        if not yes and not click.confirm(f"Update the text at {previous['url']}?", default=True):
            click.echo("Not updated.")
            return
        try:
            result = update_publication(
                inv, previous["id"], api_key=api_key, site_url=site, publisher_name=publisher_name
            )
        except QueriesChangedError as e:
            click.secho(f"Not updated: {e}", fg="yellow", err=True)
            if yes or not interactive or not click.confirm("Publish a new link instead?", default=True):
                sys.exit(1)
            mode = "new"
        except PublishError as e:
            click.secho(f"Error: {e}", fg="red", err=True)
            sys.exit(1)
        else:
            record_publication(inv.id, result, text=text_of(inv))
            click.secho("Updated.", fg="green")
            click.echo(result["url"])
            return

    click.echo(f"Publishing {inv.title!r} ({inv.id}) with {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}:")
    for q in queries:
        click.echo(f"  {q.id}. {q.name or 'Query ' + str(q.id)}")
    click.echo("Anyone with the link will see these queries, their annotations and results.")
    if not yes and not click.confirm("Publish?", default=True):
        click.echo("Not published.")
        return

    click.echo("Running the queries…")
    try:
        result = publish_investigation(inv, api_key=api_key, site_url=site, publisher_name=publisher_name)
    except PublishError as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    record_publication(inv.id, result, text=text_of(inv))
    click.secho("Published.", fg="green")
    click.echo(result["url"])


@cli.command("clone")
@click.argument("url")
@click.option("--as", "new_id", help="Save under this investigation id instead of the published one")
def clone_cmd(url: str, new_id: str | None) -> None:
    """Copy a published investigation into your own environment.

    Saves the published queries and annotations as a new local investigation
    and makes it active. Your copy is independent of the publication. No API
    key is needed; running the queries afterwards uses yours.

    \b
    jst clone https://www.jeffersonst.io/p/AbCdEf1234
    jst clone AbCdEf1234 --as my-copy
    """
    from .investigation import InvestigationError, set_active
    from .publish import PublishError, clone_publication

    try:
        inv = clone_publication(url, site_url=_site_url(), new_id=new_id)
    except (PublishError, InvestigationError) as e:
        click.secho(f"Error: {e}", fg="red", err=True)
        sys.exit(1)
    set_active(inv.id)
    click.secho(f"Cloned {inv.title!r} as {inv.id!r}.", fg="green")
    click.echo(f"It's now active. Run `jst` to open it, or `jst query run 1` to rerun a query.")


@cli.command("preview")
@click.argument("investigation_id", required=False, default=None)
@click.option(
    "--no-open",
    is_flag=True,
    help="Write the HTML file without opening a browser",
)
def preview_cmd(investigation_id: str | None, no_open: bool) -> None:
    """Render a static HTML preview of an investigation (Bokeh).

    Defaults to the most recently modified investigation.

    \b
    jst preview
    jst preview inv-1794531175
    """
    from .investigation import InvestigationError
    from .preview import ProgressSpinner, preview_investigation

    with ProgressSpinner() as progress:
        try:
            path = preview_investigation(
                client,
                investigation_id,
                open_browser=not no_open,
                progress=progress.update,
            )
        except InvestigationError as e:
            progress.fail(str(e))
            click.echo(f"Error: {e}", err=True)
            sys.exit(2)
        except Exception as e:
            progress.fail(str(e))
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
        progress.succeed(f"Wrote {path}")


@cli.command("ls")
@click.option(
    "--plain",
    is_flag=True,
    help="Print a plain text listing instead of the interactive picker",
)
def ls_cmd(plain: bool) -> None:
    """Browse projects interactively (Enter opens). Use ``--plain`` to print.

    \b
    jst ls
    jst ls --plain
    """
    from .investigation import (
        InvestigationError,
        ensure_bootstrap,
        get_active,
        open_investigation,
        run_investigation_host,
    )
    from .investigation.project_picker import run_project_picker
    from .investigation.project_store import (
        list_projects,
        load_project,
        load_project_investigation,
    )

    ensure_bootstrap()
    try:
        active = get_active()
        active_project = active.project
        active_inv = active.investigation
    except Exception:
        active_project = None
        active_inv = None

    if not plain:
        selected = run_project_picker(active_project=active_project)
        if selected is None:
            return
        try:
            inv = open_investigation(selected)
        except InvestigationError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(2)
        click.echo(f"Opened investigation {inv.id!r} ({inv.title})")
        run_investigation_host(client)
        return

    ids = list_projects()
    if not ids:
        click.echo("No projects.")
        return
    for pid in ids:
        try:
            project = load_project(pid)
            title = project.title
        except Exception:
            title = "?"
            project = None
        marker = "*" if pid == active_project or (
            active_project is None and active_inv in (project.investigations if project else [])
        ) else " "
        click.echo(f"{marker} {pid:<28} {title}")
        if project is None:
            continue
        for inv_id in project.investigations:
            inv_marker = "*" if inv_id == active_inv and (
                active_project is None or active_project == pid
            ) else " "
            try:
                inv = load_project_investigation(pid, inv_id)
                inv_title = inv.title
            except Exception:
                inv_title = "?"
            click.echo(f"  {inv_marker} {inv_id:<26} {inv_title}")


@cli.group(
    "query",
    cls=_RetiredGroup,
    retirement_message=(
        "`jst query` is retired.\n"
        "Use `jst actions query ls` (more query ops coming) "
        "and `jst actions workspace show`.\n"
        "Saved-query mutations also work inside `jst open` for now."
    ),
)
def query_group() -> None:
    """Retired — see ``jst actions query``."""


@query_group.command("ls")
def query_ls() -> None:
    """List saved queries on the active investigation.

    \b
    jst query ls
    """
    from .investigation import InvestigationError, require_active_investigation

    try:
        inv, _ctx = require_active_investigation()
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    if not inv.queries:
        click.echo("No saved queries.")
        return
    for qid in sorted(inv.queries):
        query = inv.queries[qid]
        label = query.name or ""
        click.echo(
            f"{qid:>4}  {label:<32}  "
            f"metric={len(query.metric)} entity={len(query.entity)} "
            f"series={len(query.series)}"
        )


@query_group.command("show")
@click.argument("query_id", type=int)
def query_show(query_id: int) -> None:
    """Print a saved query spec (no observations).

    \b
    jst query show 1
    """
    import yaml

    from .investigation import InvestigationError, require_active_investigation

    try:
        inv, _ctx = require_active_investigation()
        query = inv.get_query(query_id)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        yaml.safe_dump(query.to_dict(), sort_keys=False, allow_unicode=True),
        nl=False,
    )


@query_group.command("inspect")
@click.argument("query_id", type=int)
@click.option("--limit", default=50, help="Max preview rows (default 50)")
@click.option("--offset", default=0, help="Preview offset")
@click.option(
    "--format",
    default="json",
    help="Output format. Valid formats are: json, csv, pretty.",
)
def query_inspect(query_id: int, limit: int, offset: int, format: str) -> None:
    """Resolved cartesian coverage (series counts, mismatches) — no observations.

    \b
    jst query inspect 1
    jst query inspect 1 --format pretty
    """
    from .investigation import (
        InvestigationError,
        inspect_query,
        require_active_investigation,
    )

    try:
        inv, _ctx = require_active_investigation()
        payload = inspect_query(
            inv, query_id, client=client, limit=limit, offset=offset
        )
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    format_and_print(payload, format)


@query_group.command("run")
@click.argument("query_id", type=int)
@click.option(
    "--format",
    default="pretty",
    help="Output format. Valid formats are: json, csv, pretty.",
)
def query_run(query_id: int, format: str) -> None:
    """Execute a saved query (cartesian metric × entity).

    \b
    jst query run 1
    jst query run 1 --format csv
    """
    from .investigation import (
        InvestigationError,
        require_active_investigation,
        run_query,
    )

    try:
        inv, _ctx = require_active_investigation()
        results = run_query(inv, query_id, client=client)
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    format_and_print(results, format)


@query_group.command("add")
@click.option("--name", default=None, help="Display name")
@click.option(
    "--annotations",
    default=None,
    help="Freeform annotations (researcher commentary)",
)
@click.option(
    "--notes",
    default=None,
    help="Legacy alias for --annotations",
)
@click.option("--metric", multiple=True, help="Metric ID. Repeatable.")
@click.option("--entity", multiple=True, help="Entity ID. Repeatable.")
@click.option("--series", multiple=True, help="Series ID. Repeatable.")
@click.option(
    "--frequency",
    type=click.Choice(["Annual", "Quarterly", "Monthly", "Daily", "Intraday"]),
)
@click.option("--units", default=None, help="Persisted; not sent to /query yet")
@click.option("--source", default=None, help="Persisted; not sent to /query yet")
@click.option(
    "--seasonal-adjustment",
    "seasonal_adjustment",
    default=None,
    help="Persisted; not sent to /query yet",
)
@click.option("--taxonomy", default=None)
@click.option("--head", type=int, default=None)
@click.option("--tail", type=int, default=None)
@click.option("--as-of", "as_of", default=None)
@click.option("--sort-by", "sort_by", default=None)
@click.option("--order-by", "order_by", default=None)
@click.option(
    "--from-workspace",
    "from_workspace",
    is_flag=True,
    default=False,
    help="Snapshot the investigation workspace into the new query",
)
def query_add(
    name: str | None,
    annotations: str | None,
    notes: str | None,
    metric: tuple[str, ...],
    entity: tuple[str, ...],
    series: tuple[str, ...],
    frequency: str | None,
    units: str | None,
    source: str | None,
    seasonal_adjustment: str | None,
    taxonomy: str | None,
    head: int | None,
    tail: int | None,
    as_of: str | None,
    sort_by: str | None,
    order_by: str | None,
    from_workspace: bool,
) -> None:
    """Add a saved query to the active investigation (cartesian metric × entity).

    \b
    jst query add --metric gdp --entity france --entity usa --name "FR vs US"
    jst query add --from-workspace --name scratch
    """
    from .investigation import (
        InvestigationError,
        add_query,
        require_active_investigation,
    )

    try:
        inv, _ctx = require_active_investigation()
        query = add_query(
            inv,
            name=name,
            annotations=annotations,
            notes=notes,
            metric=list(metric) or None,
            entity=list(entity) or None,
            series=list(series) or None,
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
    except InvestigationError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)
    click.echo(
        f"Added query {query.id}"
        + (f" {query.name!r}" if query.name else "")
        + f" metric={len(query.metric)} entity={len(query.entity)} "
        f"series={len(query.series)}"
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
