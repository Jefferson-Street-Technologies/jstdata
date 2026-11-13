"""First-level Home destination browsers (not YAML actions)."""

from __future__ import annotations

import asyncio
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..actions.base import label_for, stage_ids
from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from ..investigation.model import Investigation, Query, Workspace
from ..models import Entity, Metric, Series, Taxonomy
from .nav import (
    DEST_ACTIONS,
    DEST_QUERIES,
    DEST_RECENT,
    DEST_SEARCH,
    DEST_WORKSPACE,
    ActionTreeRow,
    action_tree_rows,
    annotations_panel,
    get_destination,
    tree_row_height,
    workspace_counts_line,
)
from .search_drawer import SearchResultRow, fetch_search_results
from .workspace_drawer import WorkspaceResourceRow

SEARCH_TYPES = ("all", "metric", "entity", "series")
RECENT_FILTERS = ("all", "relevant", "filings", "economic")


class DestinationScreen(BaseScreen):
    """Shared chrome for first-level browsers. Esc is host ``action_back``."""

    FOOTER_CHROME: tuple[str, ...] = (
        "[bold]:[/bold] commands",
        "[bold]esc[/bold] back",
        "[bold]g[/bold] home",
        "[bold]?[/bold] help",
        "[bold]q[/bold] quit",
    )

    dest_id: str = ""

    def dest_title(self) -> str:
        return get_destination(self.dest_id).title.upper()

    def footer_status(self) -> list[str]:
        return [f"jst / {self.dest_title().lower()}"]

    def _remember(self, index: int) -> None:
        remember = getattr(self.app, "remember_dest_index", None)
        if callable(remember) and self.dest_id:
            remember(self.dest_id, index)

    def _stored_index(self) -> int:
        stored = getattr(self.app, "_dest_index", {}).get(self.dest_id, 0)
        return stored if isinstance(stored, int) else 0

    def help_bindings(self) -> list[tuple[str, str]]:
        return [
            ("j / k", "Move selection"),
            ("enter", "Open highlighted item"),
            ("esc", "Back to Home"),
            ("g", "Home"),
        ]


def _resource_label(resource: Any) -> str:
    return (
        getattr(resource, "label", None)
        or getattr(resource, "name", None)
        or getattr(resource, "id", "Unknown")
    )


# ---------------------------------------------------------------------------
# Recent (placeholder — no unified ingestion feed)
# ---------------------------------------------------------------------------


class RecentBrowser(DestinationScreen):
    dest_id = DEST_RECENT

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("f", "cycle_filter", "Filter", key_display="f"),
        Binding("enter", "confirm", "Inspect", show=False),
    ]

    DEFAULT_CSS = """
    #dest-body { height: 1fr; padding: 1 2; }
    #dest-kicker { color: #4ade80; text-style: bold; height: 1; }
    #dest-filters { color: #888; height: 1; margin-bottom: 1; }
    #dest-empty {
        color: #888;
        height: 1fr;
        padding: 2 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._filter_i = 0

    def compose_body(self) -> ComposeResult:
        with Vertical(id="dest-body"):
            yield Label("JST / RECENT", id="dest-kicker")
            yield Label("", id="dest-filters")
            yield Static("", id="dest-empty")

    def on_mount(self) -> None:
        super().on_mount()
        self._filter_i = min(self._stored_index(), len(RECENT_FILTERS) - 1)
        self._refresh()

    def footer_action_keys(self) -> list[str]:
        return ["[bold]f[/bold] filter"]

    def help_bindings(self) -> list[tuple[str, str]]:
        return [
            ("f", "Cycle All / Relevant / Filings / Economic"),
            ("esc", "Back to Home"),
            ("g", "Home"),
        ]

    def _refresh(self) -> None:
        chips = []
        for i, name in enumerate(RECENT_FILTERS):
            label = name.upper()
            if i == self._filter_i:
                chips.append(f"[bold #4ade80]{label}[/]")
            else:
                chips.append(label)
        self.query_one("#dest-filters", Label).update(" · ".join(chips))
        self.query_one("#dest-empty", Static).update(
            "No unified ingestion feed yet.\n\n"
            "Recent will list newly ingested SEC filings and economic "
            "updates here — event type, title, related entity or series, "
            "and timestamp.\n\n"
            f"Filter is set to {RECENT_FILTERS[self._filter_i]}; there is "
            "nothing to show until a feed exists.\n\n"
            "Use Search to find catalog resources in the meantime."
        )
        self._remember(self._filter_i)

    def action_cycle_filter(self) -> None:
        self._filter_i = (self._filter_i + 1) % len(RECENT_FILTERS)
        self._refresh()

    def action_cursor_down(self) -> None:
        self.action_cycle_filter()

    def action_cursor_up(self) -> None:
        self._filter_i = (self._filter_i - 1) % len(RECENT_FILTERS)
        self._refresh()

    def action_confirm(self) -> None:
        self.notify("Recent feed is not available yet", severity="warning")


# ---------------------------------------------------------------------------
# Search — reuse catalog search, Enter opens a view
# ---------------------------------------------------------------------------


class SearchBrowser(DestinationScreen):
    dest_id = DEST_SEARCH

    BINDINGS = [
        Binding("slash", "focus_filter", "Search", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("f", "cycle_type", "Type", key_display="f"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "confirm", "Open view", priority=True),
        Binding("space", "collect", "Collect", show=False),
    ]

    DEFAULT_CSS = """
    #search-body { height: 1fr; }
    #search-main { width: 1fr; height: 1fr; padding: 1 1 0 2; }
    #search-side {
        width: 36;
        height: 1fr;
        padding: 1 2;
        border-left: solid #222;
    }
    #dest-kicker { color: #4ade80; text-style: bold; height: 1; }
    #search-filters { color: #888; height: 1; margin-bottom: 1; }
    #search-input {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 0 1 0;
    }
    #search-list {
        height: 1fr;
        background: #0f0f0f;
    }
    #search-status { color: #888; height: 1; }
    #search-inspector-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #search-inspector { color: #c0c0c0; height: 1fr; }
    SearchResultRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }
    ListView > ListItem.--highlight { background: #1a3a2a; }
    """

    def __init__(self, client: JSTDataClient, workspace: Workspace) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._type_i = 0
        self._task: asyncio.Task[None] | None = None
        self._status = "Type to search the catalog"

    def compose_body(self) -> ComposeResult:
        with Horizontal(id="search-body"):
            with Vertical(id="search-main"):
                yield Label("JST / SEARCH", id="dest-kicker")
                yield Label("", id="search-filters")
                yield Input(placeholder="/ search catalog…", id="search-input")
                yield ListView(id="search-list")
                yield Label("", id="search-status")
            with Vertical(id="search-side"):
                yield Label("SELECTED", id="search-inspector-kicker")
                yield Static(
                    "Type a query. Enter opens a resource view.\n"
                    "Space adds it to the workspace.\n\n"
                    "Source and frequency filters use catalog metadata "
                    "on the resource view — there is no separate search index.",
                    id="search-inspector",
                )

    def on_mount(self) -> None:
        super().on_mount()
        stored = self._stored_index()
        self._type_i = stored if 0 <= stored < len(SEARCH_TYPES) else 0
        self._refresh_filters()
        self.query_one("#search-input", Input).focus()

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold]/[/bold] type",
            "[bold]f[/bold] type filter",
            "[bold]enter[/bold] open",
            "[bold]space[/bold] collect",
        ]

    def help_bindings(self) -> list[tuple[str, str]]:
        return [
            ("/", "Focus the search field"),
            ("f", "Cycle all / metric / entity / series"),
            ("enter", "Open the highlighted resource"),
            ("space", "Collect into the workspace"),
            ("esc", "Leave the field, then back to Home"),
        ]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "leave_filter" and not isinstance(self.focused, Input):
            return False
        if action == "collect" and isinstance(self.focused, Input):
            return False
        return True

    def _refresh_filters(self) -> None:
        chips = []
        for i, name in enumerate(SEARCH_TYPES):
            label = "ALL TYPES" if name == "all" else name.upper()
            if i == self._type_i:
                chips.append(f"[bold #4ade80]{label}[/]")
            else:
                chips.append(label)
        self.query_one("#search-filters", Label).update(" · ".join(chips))
        self._remember(self._type_i)

    def action_cycle_type(self) -> None:
        self._type_i = (self._type_i + 1) % len(SEARCH_TYPES)
        self._refresh_filters()
        self._kick_search()

    def action_focus_filter(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_leave_filter(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#search-list", ListView).focus()

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.query_one("#search-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        self.query_one("#search-list", ListView).action_cursor_up()

    def _kick_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if self._task:
            self._task.cancel()
        if not query:
            self._show_rows([], "Type to search the catalog")
            return
        if len(query) < 2:
            self._show_rows([], "Type two or more characters")
            return
        self._show_rows([], "Searching…")
        self._task = asyncio.create_task(self._run_search(query))

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        self._kick_search()

    @on(Input.Submitted, "#search-input")
    def on_search_submit(self) -> None:
        self.query_one("#search-list", ListView).focus()

    async def _run_search(self, query: str) -> None:
        rtype = SEARCH_TYPES[self._type_i]
        try:
            await asyncio.sleep(0.3)
            rows = await asyncio.to_thread(
                fetch_search_results, self.client, rtype, query
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._show_rows([], f"Search error: {exc}")
            self.notify(f"Search error: {exc}", severity="error")
            return
        if not rows:
            self._show_rows([], f"No matches for {query!r}")
            return
        self._show_rows(rows, f"{len(rows)} matches")

    def _show_rows(self, rows: list[tuple[Any, str]], status: str) -> None:
        self._status = status
        self.query_one("#search-status", Label).update(status)
        list_view = self.query_one("#search-list", ListView)
        list_view.clear()
        for resource, rtype in rows:
            list_view.append(SearchResultRow(resource, rtype))
        if rows:
            self._update_inspector(rows[0][0], rows[0][1])

    def _highlighted(self) -> SearchResultRow | None:
        child = self.query_one("#search-list", ListView).highlighted_child
        return child if isinstance(child, SearchResultRow) else None

    def _update_inspector(self, resource: Any, rtype: str) -> None:
        name = _resource_label(resource)
        source = getattr(resource, "source", None) or "—"
        freq = getattr(resource, "frequency", None) or "—"
        desc = getattr(resource, "description", None) or ""
        bits = [
            name,
            f"{rtype.upper()}  [{getattr(resource, 'id', '')}]",
            f"Source {source}  ·  Frequency {freq}",
        ]
        if desc:
            bits.append("")
            bits.append(str(desc)[:400])
        bits.append("")
        bits.append("Enter opens a view · Space collects into the workspace")
        self.query_one("#search-inspector", Static).update("\n".join(bits))

    @on(ListView.Highlighted, "#search-list")
    def on_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SearchResultRow):
            self._update_inspector(event.item.resource, event.item.resource_type)

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        row = self._highlighted()
        if row is None:
            self.notify("Nothing highlighted", severity="warning")
            return
        open_fn = getattr(self.app, "open_resource_view", None)
        if open_fn is None:
            self.notify("Host cannot open resource views", severity="error")
            return
        open_fn(
            row.resource_type,
            row.resource.id,
            label=_resource_label(row.resource),
        )

    def action_collect(self) -> None:
        row = self._highlighted()
        if row is None:
            return
        resource = row.resource
        rtype = row.resource_type
        if isinstance(resource, Taxonomy) or rtype == "taxonomy":
            self.workspace.taxonomy = resource.id
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(resource.id, _resource_label(resource))
            persist = getattr(self.app, "persist_active_workspace", None)
            if callable(persist):
                persist()
            self.notify(f"Taxonomy set to {resource.id}")
            return
        kwargs: dict[str, Any] = {
            "labels": {resource.id: _resource_label(resource)}
        }
        if isinstance(resource, Metric) or rtype == "metric":
            kwargs["metric"] = resource.id
        elif isinstance(resource, Entity) or rtype == "entity":
            kwargs["entity"] = resource.id
        else:
            kwargs["series"] = resource.id
        added = stage_ids(self.workspace, self.app, **kwargs)
        if not added:
            self.notify(f"{resource.id} already in workspace", severity="warning")
            return
        self.notify(f"Collected {resource.id}")

    @on(ListView.Selected, "#search-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, SearchResultRow):
            self.action_confirm()


# ---------------------------------------------------------------------------
# Actions — tree outline, size as progress
# ---------------------------------------------------------------------------


class ActionTreeItem(ListItem):
    def __init__(self, row: ActionTreeRow, height: int) -> None:
        super().__init__()
        self.row = row
        self.row_height = height
        self.styles.height = height
        self.styles.min_height = height

    def compose(self) -> ComposeResult:
        yield Label(self.row.title(), classes="tree-title")
        if self.row_height >= 3:
            extra = f"    {self.row.type} · {self.row.child_count} child"
            if self.row.child_count != 1:
                extra += "ren"
            yield Label(extra, classes="tree-meta")


class ActionsBrowser(DestinationScreen):
    dest_id = DEST_ACTIONS

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "confirm", "Open view", priority=True),
    ]

    DEFAULT_CSS = """
    #actions-body { height: 1fr; }
    #actions-main { width: 1fr; height: 1fr; padding: 1 1 0 2; }
    #actions-side {
        width: 36;
        height: 1fr;
        padding: 1 2;
        border-left: solid #222;
    }
    #dest-kicker { color: #4ade80; text-style: bold; height: 1; }
    #actions-sub { color: #888; height: 1; margin-bottom: 1; }
    #actions-list { height: 1fr; background: #0f0f0f; }
    #actions-inspector-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #actions-inspector { color: #c0c0c0; height: 1fr; }
    ActionTreeItem { width: 100%; padding: 0 1; }
    ActionTreeItem.-highlighted, ActionTreeItem.--highlight {
        background: #1a3a2a;
    }
    .tree-title { color: #e0e0e0; height: 1; }
    .tree-meta { color: #888; height: 1; }
    """

    def __init__(self, investigation: Investigation, active_id: int) -> None:
        super().__init__()
        self.investigation = investigation
        self.active_id = active_id
        self._rows: list[ActionTreeRow] = []

    def compose_body(self) -> ComposeResult:
        with Horizontal(id="actions-body"):
            with Vertical(id="actions-main"):
                yield Label("JST / ACTIONS", id="dest-kicker")
                yield Label("EXPLORATION TREE", id="actions-sub")
                yield ListView(id="actions-list")
            with Vertical(id="actions-side"):
                yield Label("SELECTED ACTION", id="actions-inspector-kicker")
                yield Static("", id="actions-inspector")

    def on_mount(self) -> None:
        super().on_mount()
        self._rebuild()
        self.query_one("#actions-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        n = len(self.investigation.actions)
        return [
            "[bold]j[/bold]/[bold]k[/bold] move",
            "[bold]enter[/bold] open",
            f"{n} nodes",
        ]

    def _rebuild(self) -> None:
        self._rows = action_tree_rows(self.investigation)
        height = tree_row_height(len(self._rows))
        list_view = self.query_one("#actions-list", ListView)
        list_view.clear()
        target = self._stored_index()
        ids = [row.action_id for row in self._rows]
        if self.active_id in ids:
            target = ids.index(self.active_id)
        for row in self._rows:
            list_view.append(ActionTreeItem(row, height))
        if self._rows:
            list_view.index = min(target, len(self._rows) - 1)
            self._remember(list_view.index or 0)
            self._update_inspector(self._rows[list_view.index or 0])

    def _highlighted_row(self) -> ActionTreeRow | None:
        child = self.query_one("#actions-list", ListView).highlighted_child
        if isinstance(child, ActionTreeItem):
            return child.row
        return None

    def _update_inspector(self, row: ActionTreeRow) -> None:
        action = self.investigation.get_action(row.action_id)
        args = action.args or {}
        arg_s = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "(none)"
        ctx = action.context
        ctx_s = "inherit workspace"
        if ctx is not None and not ctx.is_empty():
            ctx_s = (
                f"metric={ctx.metric} entity={ctx.entity} series={ctx.series}"
            )
        self.query_one("#actions-inspector", Static).update(
            f"{row.description}\n"
            f"type {row.type}  ·  id {row.action_id}\n"
            f"{row.child_count} child"
            f"{'' if row.child_count == 1 else 'ren'}\n\n"
            f"Args\n{arg_s}\n\n"
            f"Context\n{ctx_s}\n\n"
            "Enter opens this view."
        )

    @on(ListView.Highlighted, "#actions-list")
    def on_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, ActionTreeItem):
            self._remember(
                self.query_one("#actions-list", ListView).index or 0
            )
            self._update_inspector(event.item.row)

    def action_cursor_down(self) -> None:
        self.query_one("#actions-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#actions-list", ListView).action_cursor_up()

    def action_confirm(self) -> None:
        row = self._highlighted_row()
        if row is None:
            return
        open_fn = getattr(self.app, "open_action_from_nav", None)
        if open_fn is None:
            self.notify("Host cannot open actions", severity="error")
            return
        open_fn(row.action_id)

    @on(ListView.Selected, "#actions-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ActionTreeItem):
            self.action_confirm()


# ---------------------------------------------------------------------------
# Workspace — grouped bag, backspace removes
# ---------------------------------------------------------------------------


class GroupHeader(ListItem):
    def __init__(self, text: str) -> None:
        super().__init__(disabled=True)
        self.header_text = text

    def compose(self) -> ComposeResult:
        yield Label(self.header_text, classes="group-header")


class WorkspaceBrowser(DestinationScreen):
    dest_id = DEST_WORKSPACE

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "confirm", "Open view", priority=True),
        Binding("backspace", "remove", "Remove"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("c", "create_query", "Query from workspace", show=False),
    ]

    DEFAULT_CSS = """
    #ws-body { height: 1fr; }
    #ws-main { width: 1fr; height: 1fr; padding: 1 1 0 2; }
    #ws-side {
        width: 36;
        height: 1fr;
        padding: 1 2;
        border-left: solid #222;
    }
    #dest-kicker { color: #4ade80; text-style: bold; height: 1; }
    #ws-sub { color: #888; height: 1; margin-bottom: 1; }
    #ws-list { height: 1fr; background: #0f0f0f; }
    #ws-inspector-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #ws-inspector { color: #c0c0c0; height: 1fr; }
    .group-header { color: #4ade80; text-style: bold; height: 1; }
    WorkspaceResourceRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
    }
    ListView > ListItem.--highlight { background: #1a3a2a; }
    """

    def __init__(
        self, workspace: Workspace, labels: dict[str, str]
    ) -> None:
        super().__init__()
        self.workspace = workspace
        self.labels = labels

    def compose_body(self) -> ComposeResult:
        with Horizontal(id="ws-body"):
            with Vertical(id="ws-main"):
                yield Label("JST / WORKSPACE", id="dest-kicker")
                yield Label("", id="ws-sub")
                yield ListView(id="ws-list")
            with Vertical(id="ws-side"):
                yield Label("SELECTED RESOURCE", id="ws-inspector-kicker")
                yield Static("", id="ws-inspector")

    def on_mount(self) -> None:
        super().on_mount()
        self.rebuild()
        self.query_one("#ws-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold]enter[/bold] open",
            "[bold]backspace[/bold] remove",
            "[bold]c[/bold] query",
        ]

    def help_bindings(self) -> list[tuple[str, str]]:
        return [
            ("enter", "Open the highlighted resource"),
            ("backspace", "Remove from the workspace"),
            ("c", "Create a query from the workspace bag"),
            ("esc", "Back to Home"),
        ]

    def rebuild(self) -> None:
        ws = self.workspace
        self.query_one("#ws-sub", Label).update(
            f"{len(ws.metric)} METRICS · {len(ws.entity)} ENTITIES · "
            f"{len(ws.series)} SERIES"
        )
        list_view = self.query_one("#ws-list", ListView)
        index = list_view.index
        list_view.clear()
        groups = [
            ("METRICS", "metric", list(ws.metric)),
            ("ENTITIES", "entity", list(ws.entity)),
            ("SERIES", "series", list(ws.series)),
        ]
        if ws.taxonomy:
            list_view.append(GroupHeader("TAXONOMY"))
            list_view.append(
                WorkspaceResourceRow(
                    ws.taxonomy, "taxonomy", label_for(self.labels, ws.taxonomy)
                )
            )
        any_ids = False
        for title, rtype, ids in groups:
            list_view.append(GroupHeader(f"{title} · {len(ids)}"))
            for rid in ids:
                any_ids = True
                list_view.append(
                    WorkspaceResourceRow(rid, rtype, label_for(self.labels, rid))
                )
        if not any_ids and not ws.taxonomy:
            list_view.append(
                GroupHeader("Workspace is empty — Search and collect ids first")
            )
            self.query_one("#ws-inspector", Static).update(
                "The workspace is the live bag. Search, then space to collect."
            )
            return
        stored = self._stored_index()
        n = len(list_view.children)
        if n:
            pick = stored if index is None else index
            list_view.index = min(max(pick, 0), n - 1)
        self._refresh_inspector()

    def _highlighted_row(self) -> WorkspaceResourceRow | None:
        child = self.query_one("#ws-list", ListView).highlighted_child
        return child if isinstance(child, WorkspaceResourceRow) else None

    def _refresh_inspector(self) -> None:
        row = self._highlighted_row()
        if row is None:
            self.query_one("#ws-inspector", Static).update(
                "Select a resource.\n\n"
                "Enter opens a view · Backspace removes · c creates a query "
                "from the whole bag (a copy — later bag edits do not change "
                "saved queries)."
            )
            return
        self.query_one("#ws-inspector", Static).update(
            f"{row.display_name}\n"
            f"{row.resource_type.upper()}  [{row.resource_id}]\n\n"
            "Enter opens a view.\n"
            "Backspace removes it from the bag.\n"
            "c snapshots the whole bag as a saved query."
        )

    @on(ListView.Highlighted, "#ws-list")
    def on_highlighted(self, event: ListView.Highlighted) -> None:
        idx = self.query_one("#ws-list", ListView).index or 0
        self._remember(idx)
        self._refresh_inspector()

    def action_cursor_down(self) -> None:
        self.query_one("#ws-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#ws-list", ListView).action_cursor_up()

    def action_confirm(self) -> None:
        row = self._highlighted_row()
        if row is None:
            self.notify("Nothing highlighted", severity="warning")
            return
        open_fn = getattr(self.app, "open_resource_view", None)
        if open_fn is None:
            return
        open_fn(row.resource_type, row.resource_id, label=row.display_name)

    def action_remove(self) -> None:
        row = self._highlighted_row()
        if row is None:
            return
        rid = row.resource_id
        if row.resource_type == "taxonomy":
            self.workspace.taxonomy = None
        else:
            self.workspace.remove_id(rid)
        self.labels.pop(rid, None)
        self.notify(f"Removed {rid}")
        persist = getattr(self.app, "persist_active_workspace", None)
        if callable(persist):
            persist()
        self.rebuild()
        self.query_one("#ws-list", ListView).focus()

    def action_create_query(self) -> None:
        create = getattr(self.app, "query_from_workspace", None)
        if callable(create):
            create()

    @on(ListView.Selected, "#ws-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, WorkspaceResourceRow):
            self.action_confirm()


# ---------------------------------------------------------------------------
# Queries — create / view / delete (no edit)
# ---------------------------------------------------------------------------


class QueryListItem(ListItem):
    def __init__(self, kind: str, query: Query | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.query = query

    def compose(self) -> ComposeResult:
        if self.kind == "create":
            yield Label("+ Create from workspace", classes="query-create")
            return
        assert self.query is not None
        q = self.query
        name = q.name or f"query {q.id}"
        yield Label(f"Q{q.id} · {name}", classes="query-title")
        yield Label(
            f"    {len(q.metric)}M · {len(q.entity)}E · {len(q.series)}S"
            + (f"  ·  {q.frequency}" if q.frequency else ""),
            classes="query-meta",
        )


class QueriesBrowser(DestinationScreen):
    dest_id = DEST_QUERIES

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "confirm", "Open", priority=True),
        Binding("backspace", "delete_query", "Delete"),
        Binding("delete", "delete_query", "Delete", show=False),
        Binding("n", "create", "New from workspace", show=False),
        Binding("a", "annotate", "Annotate", show=False),
    ]

    DEFAULT_CSS = """
    #queries-body { height: 1fr; }
    #queries-main { width: 1fr; height: 1fr; padding: 1 1 0 2; }
    #queries-side {
        width: 36;
        height: 1fr;
        padding: 1 2;
        border-left: solid #222;
    }
    #dest-kicker { color: #4ade80; text-style: bold; height: 1; }
    #queries-sub { color: #888; height: 1; margin-bottom: 1; }
    #queries-list { height: 1fr; background: #0f0f0f; }
    #queries-inspector-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #queries-inspector { color: #c0c0c0; height: 1fr; }
    QueryListItem { height: 2; min-height: 2; padding: 0 1; }
    QueryListItem.-highlighted, QueryListItem.--highlight {
        background: #1a3a2a;
    }
    .query-create { color: #4ade80; height: 1; }
    .query-title { color: #e0e0e0; height: 1; }
    .query-meta { color: #888; height: 1; }
    """

    def __init__(self, investigation: Investigation) -> None:
        super().__init__()
        self.investigation = investigation

    def compose_body(self) -> ComposeResult:
        with Horizontal(id="queries-body"):
            with Vertical(id="queries-main"):
                yield Label("JST / QUERIES", id="dest-kicker")
                yield Label("Saved observation specifications", id="queries-sub")
                yield ListView(id="queries-list")
            with Vertical(id="queries-side"):
                yield Label("SELECTED QUERY", id="queries-inspector-kicker")
                yield Static("", id="queries-inspector")

    def on_mount(self) -> None:
        super().on_mount()
        self.rebuild()
        self.query_one("#queries-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold]enter[/bold] open",
            "[bold]n[/bold] from workspace",
            "[bold]a[/bold] annotate",
            "[bold]backspace[/bold] delete",
        ]

    def help_bindings(self) -> list[tuple[str, str]]:
        return [
            ("enter", "Open the highlighted query (or create)"),
            ("n", "Create from the workspace bag"),
            ("a", "Edit annotations on the highlighted query"),
            ("backspace", "Delete the highlighted query"),
            ("esc", "Back to Home"),
        ]

    def rebuild(self) -> None:
        inv = self.investigation
        list_view = self.query_one("#queries-list", ListView)
        list_view.clear()
        list_view.append(QueryListItem("create"))
        for qid in sorted(inv.queries):
            list_view.append(QueryListItem("query", inv.queries[qid]))
        n = len(inv.queries)
        stored = min(self._stored_index(), n)
        list_view.index = stored
        self._refresh_inspector()

    def _highlighted(self) -> QueryListItem | None:
        child = self.query_one("#queries-list", ListView).highlighted_child
        return child if isinstance(child, QueryListItem) else None

    def _refresh_inspector(self) -> None:
        item = self._highlighted()
        if item is None or item.kind == "create":
            ws = self.investigation.workspace
            empty = ws.is_empty()
            self.query_one("#queries-inspector", Static).update(
                "Create a new query by copying the current workspace bag.\n\n"
                + (
                    "Workspace is empty — Search and collect first."
                    if empty
                    else f"Bag {workspace_counts_line(self.investigation)} "
                    "will be copied. Later bag edits do not change this query."
                )
                + "\n\nQueries are created, viewed, and deleted. "
                "Editing comes later."
            )
            return
        q = item.query
        assert q is not None
        live = self.investigation.queries.get(q.id, q)
        self.query_one("#queries-inspector", Static).update(
            f"{live.name or f'Query {live.id}'}\n"
            f"Q{live.id}  ·  SAVED\n\n"
            f"{len(live.metric)} metrics · {len(live.entity)} entities · "
            f"{len(live.series)} series\n"
            f"Frequency: {live.frequency or 'Any'}  ·  "
            f"Source: {live.source or 'Any'}\n"
            f"Units: {live.units or 'Native'}\n\n"
            f"{annotations_panel(live.annotations)}\n\n"
            "Enter opens the JSON results view.\n"
            "a edits annotations · Backspace deletes."
        )

    @on(ListView.Highlighted, "#queries-list")
    def on_highlighted(self, event: ListView.Highlighted) -> None:
        idx = self.query_one("#queries-list", ListView).index or 0
        self._remember(idx)
        self._refresh_inspector()

    def action_cursor_down(self) -> None:
        self.query_one("#queries-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#queries-list", ListView).action_cursor_up()

    def action_create(self) -> None:
        create = getattr(self.app, "query_from_workspace", None)
        if callable(create):
            create()

    def action_annotate(self) -> None:
        item = self._highlighted()
        if item is None or item.kind != "query" or item.query is None:
            return
        edit = getattr(self.app, "edit_query_annotations", None)
        if callable(edit):
            edit(item.query.id)

    def refresh_annotations(self) -> None:
        """Host hook after annotation edits."""
        inv = getattr(self.app, "investigation", None)
        if isinstance(inv, Investigation):
            self.investigation = inv
        self._refresh_inspector()

    def action_confirm(self) -> None:
        item = self._highlighted()
        if item is None:
            return
        if item.kind == "create":
            self.action_create()
            return
        assert item.query is not None
        open_fn = getattr(self.app, "open_query_view", None)
        if callable(open_fn):
            open_fn(item.query.id)

    def action_delete_query(self) -> None:
        item = self._highlighted()
        if item is None or item.kind != "query" or item.query is None:
            return
        delete = getattr(self.app, "delete_query", None)
        if callable(delete):
            delete(item.query.id)

    @on(ListView.Selected, "#queries-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, QueryListItem):
            self.action_confirm()
