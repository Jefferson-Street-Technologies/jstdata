"""Bottom search drawer — ``:search <type> <query>``."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ..client import JSTDataClient
from ..models import Entity, Metric, Series, Taxonomy
from ..investigation.model import Workspace

SearchType = Literal["metric", "entity", "series", "taxonomy", "all"]


def _resource_label(resource: Any) -> str:
    return (
        getattr(resource, "label", None)
        or getattr(resource, "name", None)
        or getattr(resource, "id", "Unknown")
    )


def _resource_type_of(resource: Any, fallback: str) -> str:
    if isinstance(resource, Metric):
        return "metric"
    if isinstance(resource, Entity):
        return "entity"
    if isinstance(resource, Series):
        return "series"
    if isinstance(resource, Taxonomy):
        return "taxonomy"
    return fallback


def _fetch_all_taxonomies(client: JSTDataClient) -> list[Taxonomy]:
    """Page through ``GET /taxonomy`` until exhausted."""
    out: list[Taxonomy] = []
    offset = 0
    page = 100
    while True:
        batch = client.list_taxonomies(limit=page, offset=offset)
        out.extend(batch)
        if len(batch) < page:
            break
        offset += page
        if offset >= 10_000:
            break
    return out


def _filter_taxonomies(taxonomies: list[Taxonomy], query: str) -> list[Taxonomy]:
    needle = query.strip().lower()
    if not needle:
        return list(taxonomies)
    return [
        t
        for t in taxonomies
        if needle in t.id.lower() or needle in (t.name or "").lower()
    ]


class SearchResultRow(ListItem):
    def __init__(self, resource: Any, resource_type: str) -> None:
        super().__init__()
        self.resource = resource
        self.resource_type = resource_type

    def compose(self) -> ComposeResult:
        name = _resource_label(self.resource)
        yield Label(
            f"{self.resource_type.upper():<8} {name}  [{self.resource.id}]",
            classes="search-row-label",
        )


class SearchDrawer(ModalScreen[None]):
    """Non-dimming bottom drawer for catalog search → workspace add."""

    DEFAULT_CSS = """
    SearchDrawer {
        align: center bottom;
        background: transparent;
    }

    #search-drawer-panel {
        width: 100%;
        height: 40%;
        min-height: 12;
        background: #111111;
        border-top: tall #4ade80;
        padding: 1 1;
    }

    #search-drawer-title {
        text-style: bold;
        color: #4ade80;
        height: 1;
        margin-bottom: 1;
    }

    #search-drawer-list {
        height: 1fr;
        background: #0a0a0a;
        border: solid #222;
    }

    SearchResultRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }

    SearchResultRow:hover,
    SearchResultRow.-highlighted {
        background: #1a3a1a;
    }

    .search-row-label {
        width: 100%;
        height: 1;
        color: #e0e0e0;
        overflow: hidden;
    }

    ListView > ListItem {
        height: 1;
        min-height: 1;
        max-height: 1;
    }

    #search-drawer-hint {
        color: #888;
        height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "add_highlighted", "Add", show=False),
    ]

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        resource_type: SearchType,
        query: str,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.resource_type: SearchType = resource_type
        self.search_query = query

    def compose(self) -> ComposeResult:
        with Vertical(id="search-drawer-panel"):
            yield Label(
                f"SEARCH // {self.resource_type.upper()} // {self.search_query}",
                id="search-drawer-title",
            )
            yield ListView(id="search-drawer-list")
            enter_label = (
                "set taxonomy" if self.resource_type == "taxonomy" else "add"
            )
            yield Label(
                f"[bold]enter[/bold] {enter_label}  ·  [bold]j[/bold]/[bold]k[/bold]  ·  "
                "[bold]esc[/bold] close",
                id="search-drawer-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one("#search-drawer-list", ListView).focus()
        asyncio.create_task(self._run_search())

    async def _run_search(self) -> None:
        list_view = self.query_one("#search-drawer-list", ListView)
        list_view.clear()
        try:
            rows = await asyncio.to_thread(self._fetch_results)
        except Exception as e:
            self.notify(f"Search error: {e}", severity="error")
            return
        if not rows:
            list_view.append(
                ListItem(Label("No results", classes="search-row-label"), disabled=True)
            )
            return
        for resource, rtype in rows:
            list_view.append(SearchResultRow(resource, rtype))

    def _fetch_results(self) -> list[tuple[Any, str]]:
        """Return ``(resource, display_type)`` pairs for the list."""
        if self.resource_type == "taxonomy":
            taxonomies = _filter_taxonomies(
                _fetch_all_taxonomies(self.client), self.search_query
            )
            return [(t, "taxonomy") for t in taxonomies]

        if self.resource_type == "all":
            results = self.client.search(self.search_query, limit=40)
            return [(r, _resource_type_of(r, "series")) for r in results]

        if self.resource_type == "entity":
            results = self.client.search_entities(self.search_query, limit=40)
        elif self.resource_type == "series":
            results = self.client.search_series(self.search_query, limit=40)
        else:
            results = self.client.search_metrics(self.search_query, limit=40)
        return [(r, self.resource_type) for r in results]

    def _add_resource(self, resource: Any, resource_type: str) -> bool:
        if isinstance(resource, Taxonomy) or resource_type == "taxonomy":
            if self.workspace.taxonomy == resource.id:
                return False
            self.workspace.taxonomy = resource.id
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(resource.id, _resource_label(resource))
            return True

        if isinstance(resource, Metric) or resource_type == "metric":
            added = self.workspace.add_metric(resource.id)
        elif isinstance(resource, Entity) or resource_type == "entity":
            added = self.workspace.add_entity(resource.id)
        elif isinstance(resource, Series) or resource_type == "series":
            added = self.workspace.add_series(resource.id)
        else:
            return False
        if added and hasattr(self.app, "remember_label"):
            self.app.remember_label(resource.id, _resource_label(resource))
        return added

    @on(ListView.Selected, "#search-drawer-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SearchResultRow):
            self._try_add(event.item)

    def action_add_highlighted(self) -> None:
        child = self.query_one("#search-drawer-list", ListView).highlighted_child
        if isinstance(child, SearchResultRow):
            self._try_add(child)

    def _try_add(self, row: SearchResultRow) -> None:
        if not self._add_resource(row.resource, row.resource_type):
            if row.resource_type == "taxonomy":
                self.notify(
                    f"Taxonomy already set to {row.resource.id}", severity="warning"
                )
            else:
                self.notify(
                    f"{row.resource.id} already in workspace", severity="warning"
                )
            return
        if row.resource_type == "taxonomy":
            self.notify(f"Taxonomy set to {row.resource.id}")
        else:
            self.notify(f"Added {row.resource.id}")
        persist = getattr(self.app, "persist_active_workspace", None)
        if callable(persist):
            persist()
        refresh = getattr(self.app, "refresh_workspace_drawer", None)
        if callable(refresh):
            refresh()

    def action_cursor_down(self) -> None:
        self.query_one("#search-drawer-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#search-drawer-list", ListView).action_cursor_up()

    def action_dismiss(self) -> None:
        self.dismiss()
