"""Console action — one-pane catalog search that stages into the workspace."""

from __future__ import annotations

import asyncio
from typing import TypeAlias

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView

from ..client import JSTDataClient
from ..models import Entity, Metric, Series, Resource as ApiResource
from ..investigation.model import Workspace
from .base import ActionArgument, ActionBinding, ActionSpec, register
from .screen import BaseScreen

Resource: TypeAlias = Series | Entity | Metric | ApiResource

PREFETCH_LIMIT = 201
LOCAL_CAP = 200


def _normalize_relations(relation: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    if relation is None:
        return None
    if isinstance(relation, str):
        value = relation.strip()
        return [value] if value else None
    items = [str(v).strip() for v in relation if str(v).strip()]
    return items or None


class SearchResultRow(ListItem):
    """A row in the search results list."""

    def __init__(self, resource: Resource):
        super().__init__()
        self.resource = resource

    def compose(self) -> ComposeResult:
        res = self.resource
        name = getattr(res, "label", getattr(res, "name", "Unknown"))
        res_type = (
            "SERIES"
            if isinstance(res, Series)
            else "ENTITY"
            if isinstance(res, Entity)
            else "METRIC"
        )
        source = getattr(res, "source", "N/A")

        yield Horizontal(
            Label(f"{name}", classes="col-name"),
            Label(f"{res.id[:15]:<15}", classes="col-id"),
            Label(f"{source[:10]:<10}", classes="col-src"),
            Label(f"{res_type:<10}", classes="col-type"),
        )


class WorkspaceScreen(BaseScreen):
    """Single-pane search: prefetch a catalog, filter locally when it fits."""

    BINDINGS = [
        Binding("slash", "focus_filter", "Search", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
    ]

    DEFAULT_CSS = """
    #workspace-body {
        height: 1fr;
    }

    .pane-container {
        border: solid #222;
        background: #0f0f0f;
        height: 1fr;
    }

    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    .table-header {
        height: 1;
        background: #111;
        border-bottom: solid #333;
        padding: 0 1;
    }
    .table-header Label {
        color: #888;
        text-style: bold;
    }

    #search-input {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 1;
    }

    #results-list {
        height: 1fr;
        background: #0f0f0f;
    }

    .col-name { width: 55%; height: 1; overflow: hidden; }
    .col-id   { width: 25%; height: 1; overflow: hidden; }
    .col-src  { width: 10%; height: 1; overflow: hidden; }
    .col-type { width: 10%; height: 1; overflow: hidden; }

    ListView > ListItem,
    SearchResultRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }

    SearchResultRow > Horizontal {
        height: 1;
        min-height: 1;
        max-height: 1;
        overflow: hidden;
    }

    SearchResultRow:focus,
    SearchResultRow.-highlighted {
        background: #1a3a1a;
        color: #4ade80;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        taxonomy: str | None = None,
        resource_type: str | None = None,
        relation: str | list[str] | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.taxonomy = taxonomy or None
        self.taxonomy_name = taxonomy
        self.resource_type = (resource_type or "").strip().lower() or None
        self.relations = _normalize_relations(relation)
        if self.resource_type not in (None, "entity", "metric", "series"):
            self.resource_type = None

        self.search_task: asyncio.Task[None] | None = None
        self.catalog: list[Resource] = []
        self.large_search_space = False

    def _relation_label(self) -> str:
        if not self.relations:
            return ""
        if len(self.relations) == 1:
            return self.relations[0]
        return f"{len(self.relations)} relations"

    def compose_body(self) -> ComposeResult:
        with Vertical(id="workspace-body"):
            with Vertical(classes="pane-container", id="results-pane"):
                yield Label(
                    "RESULTS // SEARCH",
                    classes="pane-header",
                    id="results-header",
                )
                yield Input(
                    placeholder="/ search catalog…",
                    id="search-input",
                )
                with Horizontal(classes="table-header"):
                    yield Label("NAME", classes="col-name")
                    yield Label("ID", classes="col-id")
                    yield Label("SRC", classes="col-src")
                    yield Label("TYPE", classes="col-type")
                yield ListView(id="results-list")

    def on_mount(self) -> None:
        super().on_mount()
        self._apply_search_chrome()
        # No catalog/query on open — prefetch lazily when the user searches.
        self.query_one("#results-list", ListView).focus()

    def _apply_search_chrome(self) -> None:
        tax = self.taxonomy_name or self.taxonomy or ""
        rtype = self.resource_type.upper() if self.resource_type else ""
        rel = self._relation_label()
        if rel and rtype:
            header = f"RESULTS // {rtype} // {rel}"
            placeholder = f"/ search {rtype.lower()} linked via {rel}…"
        elif rel:
            header = f"RESULTS // ENTITY // {rel}"
            placeholder = f"/ search entities linked via {rel}…"
        elif tax and rtype:
            header = f"RESULTS // {rtype} IN {tax}"
            placeholder = f"/ search {rtype.lower()} in {tax}…"
        elif tax:
            header = f"RESULTS // TAXONOMY {tax}"
            placeholder = f"/ search within {tax}…"
        elif rtype:
            header = f"RESULTS // {rtype}"
            placeholder = f"/ search {rtype.lower()}…"
        else:
            header = "RESULTS // SEARCH"
            placeholder = "/ search catalog…"
        self.query_one("#results-header").update(header)
        self.query_one("#search-input", Input).placeholder = placeholder

    def footer_status(self) -> list[str]:
        parts = super().footer_status()
        if self.taxonomy:
            parts.append(f"tax {self.taxonomy}")
        if self.relations:
            rel = self._relation_label()
            if len(rel) > 28:
                rel = rel[:25] + "…"
            parts.append(f"rel {rel}")
        if self.resource_type:
            parts.append(self.resource_type)
        parts.append("server search" if self.large_search_space else "local")
        return parts

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold]/[/bold] search",
            "[bold]enter[/bold] add",
        ]

    def _refresh_help_hint(self) -> None:
        self.refresh_footer()

    def _show_rows(self, resources: list[Resource]) -> None:
        list_view = self.query_one("#results-list", ListView)
        list_view.clear()
        for resource in resources:
            list_view.append(SearchResultRow(resource))

    def _filter_catalog(self, query: str) -> list[Resource]:
        q = query.lower().strip()
        if not q:
            return list(self.catalog)
        hits: list[Resource] = []
        for resource in self.catalog:
            name = (
                getattr(resource, "label", None)
                or getattr(resource, "name", None)
                or ""
            )
            if q in name.lower() or q in resource.id.lower():
                hits.append(resource)
        return hits

    @work(exclusive=True, group="prefetch")
    async def run_prefetch(self) -> None:
        """Load a first page so the pane is never empty; note if the set is huge."""
        try:
            entities, metrics, series = await self._fetch_catalog()
        except Exception as e:
            self.notify(f"Could not load catalog: {e}", severity="error")
            return
        self.catalog = [*metrics, *entities, *series]
        self.large_search_space = (
            len(entities) > LOCAL_CAP
            or len(metrics) > LOCAL_CAP
            or len(series) > LOCAL_CAP
        )
        self._refresh_help_hint()
        # Re-apply anything typed while the prefetch was in flight.
        self._apply_query(self.query_one("#search-input", Input).value)

    async def _fetch_catalog(
        self,
    ) -> tuple[list[Entity], list[Metric], list[Series]]:
        rtype = self.resource_type
        tax = self.taxonomy
        rel = self.relations
        if rtype == "entity":
            entities = await asyncio.to_thread(
                self.client.search_entities,
                None,
                taxonomy=tax,
                relation=rel,
                limit=PREFETCH_LIMIT,
            )
            return list(entities), [], []
        if rtype == "metric":
            metrics = await asyncio.to_thread(
                self.client.search_metrics,
                None,
                taxonomy=tax,
                limit=PREFETCH_LIMIT,
            )
            return [], list(metrics), []
        if rtype == "series":
            series = await asyncio.to_thread(
                self.client.list_series, PREFETCH_LIMIT
            )
            return [], [], list(series)
        if rel:
            entities = await asyncio.to_thread(
                self.client.search_entities,
                None,
                taxonomy=tax,
                relation=rel,
                limit=PREFETCH_LIMIT,
            )
            if tax:
                metrics = await asyncio.to_thread(
                    self.client.get_taxonomy_metrics, tax, PREFETCH_LIMIT
                )
                return list(entities), list(metrics), []
            return list(entities), [], []
        if tax:
            entities, metrics = await asyncio.gather(
                asyncio.to_thread(
                    self.client.get_taxonomy_entities, tax, PREFETCH_LIMIT
                ),
                asyncio.to_thread(
                    self.client.get_taxonomy_metrics, tax, PREFETCH_LIMIT
                ),
            )
            return list(entities), list(metrics), []
        entities, metrics, series = await asyncio.gather(
            asyncio.to_thread(
                self.client.search_entities, None, limit=PREFETCH_LIMIT
            ),
            asyncio.to_thread(
                self.client.search_metrics, None, limit=PREFETCH_LIMIT
            ),
            asyncio.to_thread(self.client.list_series, PREFETCH_LIMIT),
        )
        return list(entities), list(metrics), list(series)

    def action_focus_filter(self) -> None:
        if not self.catalog:
            self.run_prefetch()
        self.query_one("#search-input", Input).focus()

    def action_leave_filter(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#results-list", ListView).focus()

    def action_cursor_down(self) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            return
        if focused and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            return
        if focused and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self._apply_query(event.value)

    def _apply_query(self, value: str) -> None:
        if self.search_task:
            self.search_task.cancel()
        query = value.strip()
        if not query:
            self._show_rows(self.catalog)
            return
        # Empty catalog (idle mount): server-search until prefetch fills local.
        if self.large_search_space or not self.catalog:
            if len(query) < 2:
                self._show_rows(self._filter_catalog(query))
                return
            self.search_task = asyncio.create_task(self._do_server_search(query))
            return
        self._show_rows(self._filter_catalog(query))

    async def _do_server_search(self, query: str) -> None:
        try:
            await asyncio.sleep(0.3)
            results = await self._search_remote(query)
            self._show_rows(results)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.notify(f"Search error: {e}", severity="error")

    async def _search_remote(self, query: str) -> list[Resource]:
        tax = self.taxonomy
        rel = self.relations
        rtype = self.resource_type
        if rtype == "entity":
            return list(
                await asyncio.to_thread(
                    self.client.search_entities,
                    query,
                    taxonomy=tax,
                    relation=rel,
                    limit=20,
                )
            )
        if rtype == "metric":
            return list(
                await asyncio.to_thread(
                    self.client.search_metrics, query, taxonomy=tax, limit=20
                )
            )
        if rtype == "series":
            return list(
                await asyncio.to_thread(self.client.search_series, query, limit=20)
            )
        if rel:
            entities = list(
                await asyncio.to_thread(
                    self.client.search_entities,
                    query,
                    taxonomy=tax,
                    relation=rel,
                    limit=20,
                )
            )
            if tax:
                metrics = list(
                    await asyncio.to_thread(
                        self.client.search_metrics, query, taxonomy=tax, limit=15
                    )
                )
                return [*metrics, *entities]
            return entities
        if tax:
            entities, metrics = await asyncio.gather(
                asyncio.to_thread(
                    self.client.search_entities, query, taxonomy=tax, limit=15
                ),
                asyncio.to_thread(
                    self.client.search_metrics, query, taxonomy=tax, limit=15
                ),
            )
            return [*metrics, *entities]
        return list(await asyncio.to_thread(self.client.search, query, limit=20))

    @on(Input.Submitted, "#search-input")
    def on_search_submit(self) -> None:
        self.query_one("#results-list", ListView).focus()

    @on(ListView.Selected, "#results-list")
    def add_to_workspace(self, event: ListView.Selected) -> None:
        resource = event.item.resource
        if not self._add_resource_to_workspace(resource):
            self.notify(f"{resource.id} is already in workspace", severity="warning")
            return
        self.notify("Added to workspace")

    def _resource_label(self, resource: Resource) -> str:
        return (
            getattr(resource, "label", None)
            or getattr(resource, "name", None)
            or resource.id
        )

    def _add_resource_to_workspace(self, resource: Resource) -> bool:
        if isinstance(resource, Metric):
            added = self.workspace.add_metric(resource.id)
        elif isinstance(resource, Entity):
            added = self.workspace.add_entity(resource.id)
        elif isinstance(resource, Series):
            added = self.workspace.add_series(resource.id)
        elif resource.id in self.workspace.resource_ids():
            return False
        else:
            added = self.workspace.add_series(resource.id)
        if added:
            self.app.remember_label(resource.id, self._resource_label(resource))
        return added


def create_console_screen(
    client, workspace, taxonomy=None, resource_type=None, relation=None, **kwargs
):
    return WorkspaceScreen(
        client,
        workspace,
        taxonomy=taxonomy,
        resource_type=resource_type,
        relation=relation,
    )


CONSOLE = register(
    ActionSpec(
        id="console",
        name="Console",
        description=(
            "Search metrics, entities, and series, then stage them into the workspace."
        ),
        create_screen=create_console_screen,
        arguments=(
            ActionArgument(
                name="taxonomy",
                type="string",
                description=(
                    "Restrict search to entities and metrics in this taxonomy "
                    "(slug, e.g. sec-central-index-key)"
                ),
            ),
            ActionArgument(
                name="relation",
                type="string",
                multiple=True,
                description=(
                    "Restrict entity search to those linked to an anchor via a typed "
                    "relationship (<relationship_type>:<to_entity_id>, "
                    "e.g. classified_as:sic:3674). Repeatable; OR'd."
                ),
            ),
            ActionArgument(
                name="resource_type",
                type="string",
                description="Restrict search to series, metric, or entity",
                choices=("series", "metric", "entity"),
            ),
        ),
        bindings=(
            ActionBinding("/", "focus_filter", "Search the catalog"),
            ActionBinding("enter", "submit", "Focus results / add highlighted item"),
            ActionBinding("j / k", "cursor", "Move highlight down / up"),
            ActionBinding("escape", "leave_filter", "Leave the search field"),
        ),
        example=(
            "jst action add console --args "
            "'{\"relation\":[\"classified_as:sic:3674\"],"
            "\"resource_type\":\"entity\"}'"
        ),
    )
)
