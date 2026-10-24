"""Bottom search drawer — ``:search <type> <query>``."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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


def result_primary_name(
    resource: Any,
    resource_type: str,
    *,
    prefer_entity: bool = False,
) -> str:
    """Scan column: entity under a metric cluster, otherwise the resource label."""
    if prefer_entity and (
        resource_type == "series" or isinstance(resource, Series)
    ):
        entities = getattr(resource, "entities", None) or []
        if entities:
            return getattr(entities[0], "label", None) or entities[0].id
    return _resource_label(resource)


def resource_inspector_copy(resource: Any, resource_type: str) -> str:
    """Structured inspector panel for a highlighted catalog resource."""
    name = _resource_label(resource)
    rid = getattr(resource, "id", "—")
    source = getattr(resource, "source", None) or "—"
    freq = getattr(resource, "frequency", None) or "—"
    units = getattr(resource, "units", None) or "—"
    desc = (getattr(resource, "description", None) or "").strip()
    entity = "—"
    if resource_type == "series" or isinstance(resource, Series):
        entities = getattr(resource, "entities", None) or []
        if entities:
            entity = getattr(entities[0], "label", None) or entities[0].id
        metric = getattr(resource, "metric_id", None) or "—"
        blurb = " · ".join(p for p in (entity, freq, units) if p and p != "—")
        lines = [
            f"[bold]{name}[/]",
            f"[#888]{blurb or rid}[/]",
            "",
            "[bold #888]SOURCE[/]                  [bold #888]FREQUENCY[/]",
            f"{source:<24}{freq}",
            "[bold #888]ENTITY[/]                  [bold #888]UNITS[/]",
            f"{entity:<24}{units}",
            f"[#888]Metric {metric}  ·  {rid}[/]",
        ]
    elif resource_type == "metric" or isinstance(resource, Metric):
        variants = getattr(resource, "variants", None) or []
        if variants and source == "—":
            source = getattr(variants[0], "source", None) or "—"
        if variants and freq == "—":
            freq = getattr(variants[0], "frequency", None) or "—"
        if variants and units == "—":
            units = getattr(variants[0], "units", None) or "—"
        lines = [
            f"[bold]{name}[/]",
            f"[#888]{rid}[/]",
            "",
            "[bold #888]SOURCE[/]                  [bold #888]FREQUENCY[/]",
            f"{source:<24}{freq}",
            "[bold #888]UNITS[/]",
            str(units),
        ]
        if desc:
            lines.extend(["", desc[:500]])
    elif resource_type == "entity" or isinstance(resource, Entity):
        lines = [
            f"[bold]{name}[/]",
            f"[#888]{rid}[/]",
            "",
            "[bold #888]TYPE[/]",
            "Entity",
        ]
    else:
        lines = [
            f"[bold]{name}[/]",
            f"[#888]{rid}[/]",
        ]
    lines.extend(
        [
            "",
            "[bold #4ade80]AVAILABLE ACTIONS[/]",
            "↵  Open view",
            "Space  Add to workspace",
        ]
    )
    return "\n".join(lines)


TYPE_HEADINGS = frozenset({"METRIC", "ENTITY", "SERIES", "TAXONOMY"})


def _metric_id(series: Any) -> str:
    return str(getattr(series, "metric_id", None) or "").strip()


def _series_heading(group: list[Any]) -> str:
    label = _resource_label(group[0])
    if " for " in label:
        return label.split(" for ", 1)[0].upper()
    mid = _metric_id(group[0]) or "SERIES"
    return mid.replace("-", " ").upper()


def clustered_search_items(
    rows: list[tuple[Any, str]],
) -> list[tuple[str, Any | None, str | None]]:
    """Group series by shared metric_id; mixed series stay under SERIES.

    A metric-name heading is only used when two or more series actually share
    a metric. Returns ``("header", title, None)`` or ``("row", resource, type)``.
    """
    from collections import OrderedDict

    buckets: dict[str, list[Any]] = OrderedDict()
    for resource, rt in rows:
        buckets.setdefault(rt, []).append(resource)
    multi_type = sum(1 for items in buckets.values() if items) > 1
    out: list[tuple[str, Any | None, str | None]] = []

    def emit_series(group: list[Any], heading: str | None) -> None:
        if heading:
            out.append(("header", heading, None))
        for series in group:
            out.append(("row", series, "series"))

    for rt, items in buckets.items():
        if rt == "series":
            by_metric: OrderedDict[str, list[Any]] = OrderedDict()
            leftover: list[Any] = []
            for series in items:
                key = _metric_id(series)
                if key:
                    by_metric.setdefault(key, []).append(series)
                else:
                    leftover.append(series)
            clusters: list[list[Any]] = []
            for group in by_metric.values():
                if len(group) >= 2:
                    clusters.append(group)
                else:
                    leftover.extend(group)
            for group in clusters:
                emit_series(group, _series_heading(group))
            if leftover:
                heading = "SERIES" if multi_type or clusters else None
                emit_series(leftover, heading)
            continue
        if multi_type:
            out.append(("header", rt.upper(), None))
        for resource in items:
            out.append(("row", resource, rt))
    return out


class SearchResultRow(ListItem):
    def __init__(
        self,
        resource: Any,
        resource_type: str,
        *,
        prefer_entity: bool = False,
    ) -> None:
        super().__init__()
        self.resource = resource
        self.resource_type = resource_type
        self.prefer_entity = prefer_entity

    def compose(self) -> ComposeResult:
        name = result_primary_name(
            self.resource,
            self.resource_type,
            prefer_entity=self.prefer_entity,
        )
        with Horizontal(classes="search-row"):
            yield Label(self.resource_type.upper(), classes="search-type")
            yield Label(name, classes="search-name")
            yield Label(str(self.resource.id), classes="search-id")


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
        border-left: solid #111111;
    }

    SearchResultRow:hover,
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }

    .search-row { height: 1; width: 100%; }
    .search-type { width: 8; color: #666; height: 1; }
    .search-name { width: 1fr; color: #d0d0d0; height: 1; }
    .search-id { width: 16; color: #666; height: 1; text-align: right; }
    ListView > ListItem.--highlight .search-type { color: #4ade80; }
    ListView > ListItem.--highlight .search-name { color: #f5f5f5; }

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
        return fetch_search_results(
            self.client, self.resource_type, self.search_query
        )

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


def fetch_search_results(
    client: JSTDataClient,
    resource_type: str,
    query: str,
    *,
    limit: int = 40,
) -> list[tuple[Any, str]]:
    """Return ``(resource, display_type)`` pairs for catalog search UIs."""
    if resource_type == "taxonomy":
        taxonomies = _filter_taxonomies(_fetch_all_taxonomies(client), query)
        return [(t, "taxonomy") for t in taxonomies]

    if resource_type == "all":
        results = client.search(query, limit=limit)
        return [(r, _resource_type_of(r, "series")) for r in results]

    if resource_type == "entity":
        results = client.search_entities(query, limit=limit)
    elif resource_type == "series":
        results = client.search_series(query, limit=limit)
    else:
        results = client.search_metrics(query, limit=limit)
    return [(r, resource_type) for r in results]
