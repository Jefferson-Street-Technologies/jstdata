"""Home — persistent three-pane investigation shell."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..actions.base import ActionBinding, ActionSpec, label_for, register, stage_ids
from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from ..investigation.model import Investigation, Workspace
from ..models import Entity, Metric, Series, Taxonomy
from .browsers import (
    RECENT_FILTERS,
    SEARCH_TYPES,
    ActionTreeItem,
    GroupHeader,
    QueryListItem,
    _resource_label,
)
from .nav import (
    DESTINATIONS,
    Destination,
    action_tree_rows,
    inspector_copy,
    render_action_graph,
    tree_row_height,
    workspace_counts_line,
)
from .search_drawer import (
    TYPE_HEADINGS,
    SearchResultRow,
    clustered_search_items,
    fetch_search_results,
    resource_inspector_copy,
)
from .workspace_drawer import WorkspaceResourceRow


class DestinationCard(Static):
    """One of the five Home destinations — large, numbered, selectable."""

    def __init__(self, dest: Destination) -> None:
        super().__init__()
        self.dest = dest

    def compose(self) -> ComposeResult:
        yield Label(self.dest.number, classes="dest-num")
        yield Label(self.dest.title, classes="dest-title")
        yield Label(self.dest.blurb, classes="dest-blurb")


class FilterRow(ListItem):
    def __init__(self, key: str, title: str) -> None:
        super().__init__()
        self.key = key
        self.title = title

    def compose(self) -> ComposeResult:
        yield Label(self.title)


class HomeScreen(BaseScreen):
    """Persistent Home: options left, data top-right, graph bottom-right."""

    BINDINGS = [
        Binding("escape", "leave", "Back", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("down", "cursor_down", "Cursor Down", show=False),
        Binding("up", "cursor_up", "Cursor Up", show=False),
        Binding("enter", "confirm", "Open", priority=True),
        Binding("1", "jump_1", "Recent", show=False),
        Binding("2", "jump_2", "Search", show=False),
        Binding("3", "jump_3", "Actions", show=False),
        Binding("4", "jump_4", "Workspace", show=False),
        Binding("5", "jump_5", "Queries", show=False),
        Binding("slash", "focus_filter", "Search", show=False),
        Binding("f", "cycle_filter", "Filter", show=False),
        Binding("space", "collect", "Collect", show=False),
        Binding("backspace", "remove", "Remove", show=False),
        Binding("delete", "remove", "Remove", show=False),
        Binding("c", "create_query", "Query from workspace", show=False),
        Binding("n", "create_query", "Query from workspace", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
    ]

    FOOTER_CHROME: tuple[str, ...] = (
        "[bold #e0e0e0]:[/] [#888]commands[/]",
        "[bold #e0e0e0]?[/] [#888]help[/]",
        "[bold #e0e0e0]q[/] [#888]quit[/]",
    )

    DEFAULT_CSS = """
    #home-body { height: 1fr; }
    #home-header {
        height: auto;
        min-height: 3;
        padding: 1 2 0 2;
    }
    #home-identity { width: 1fr; height: auto; }
    #home-title {
        width: 1fr;
        color: #e0e0e0;
        text-style: bold;
        height: 1;
    }
    #home-subtitle {
        width: 1fr;
        color: #888;
        height: 1;
    }
    #home-subtitle.-empty { display: none; }
    #home-main { height: 1fr; }
    #home-nav {
        width: 42%;
        height: 1fr;
        padding: 0 1 0 1;
    }
    #home-data {
        width: 1fr;
        height: 1fr;
        padding: 1 2 1 2;
        border-left: solid #222;
    }
    #home-data-kicker {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    #home-data-scroll {
        height: 1fr;
        overflow-y: auto;
    }
    #home-data-body {
        color: #d1fae5;
        height: auto;
    }
    #home-graph-kicker {
        height: 1;
        color: #4ade80;
        text-style: bold;
        margin-top: 1;
    }
    #home-graph {
        height: 6;
        min-height: 6;
        max-height: 6;
        background: transparent;
        border: none;
        padding: 0;
        color: #4ade80;
    }
    #search-input {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #166534;
        padding: 0 1;
        display: none;
    }
    #search-filters {
        height: 1;
        color: #888;
        padding: 0 1;
        display: none;
    }
    #home-dests {
        height: 1fr;
        overflow-y: auto;
    }
    #home-list {
        height: 1fr;
        background: #0f0f0f;
        overflow-y: auto;
    }
    DestinationCard {
        height: 1fr;
        min-height: 3;
        padding: 1 1 1 1;
        border: none;
        border-left: solid #0a0a0a;
        margin: 0;
        background: transparent;
    }
    DestinationCard.-selected {
        background: #111a14;
        border-left: solid #4ade80;
    }
    .dest-num { color: #4ade80; text-style: bold; height: 1; }
    .dest-title { color: #e0e0e0; text-style: bold; height: 1; }
    .dest-blurb { color: #888; height: 1; }
    FilterRow, SearchResultRow, WorkspaceResourceRow, QueryListItem, ActionTreeItem {
        width: 100%;
        padding: 0 1;
    }
    SearchResultRow {
        border-left: solid #0a0a0a;
        padding: 0 1 0 0;
    }
    SearchResultRow, WorkspaceResourceRow, FilterRow, GroupHeader {
        height: 1;
        min-height: 1;
        max-height: 1;
    }
    QueryListItem { height: 2; min-height: 2; }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    .search-row { height: 1; width: 100%; }
    .search-type { width: 8; color: #555; height: 1; }
    .search-name { width: 1fr; color: #c8c8c8; height: 1; }
    .search-id { width: 16; color: #555; height: 1; text-align: right; }
    ListView > ListItem.--highlight .search-type { color: #4ade80; }
    ListView > ListItem.--highlight .search-name { color: #f5f5f5; }
    .tree-title { color: #e0e0e0; height: 1; }
    .tree-meta { color: #86efac; height: 1; }
    .group-header { color: #4ade80; text-style: bold; height: 1; }
    .query-create { color: #4ade80; height: 1; }
    .query-title { color: #e0e0e0; height: 1; }
    .query-meta { color: #888; height: 1; }
    HomeScreen.-narrow #home-data { display: none; }
    HomeScreen.-narrow #home-nav { width: 100%; }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        title: str = "Investigation",
        body: str = "",
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.home_title = title
        self.body = body
        self._index = 0
        self._dest: Optional[str] = None
        self._type_i = 0
        self._search_task: asyncio.Task[None] | None = None

    def compose_body(self) -> ComposeResult:
        with Vertical(id="home-body"):
            with Horizontal(id="home-header"):
                with Vertical(id="home-identity"):
                    yield Label(self.home_title, id="home-title")
                    yield Label("", id="home-subtitle")
            with Horizontal(id="home-main"):
                with Vertical(id="home-nav"):
                    yield Input(placeholder="/ search catalog…", id="search-input")
                    yield Label("", id="search-filters")
                    with VerticalScroll(id="home-dests"):
                        for dest in DESTINATIONS:
                            yield DestinationCard(dest)
                    yield ListView(id="home-list")
                with Vertical(id="home-data"):
                    yield Label("", id="home-data-kicker")
                    with VerticalScroll(id="home-data-scroll"):
                        yield Static("", id="home-data-body")
                    yield Label("", id="home-graph-kicker")
                    yield Static("", id="home-graph")

    def on_mount(self) -> None:
        super().on_mount()
        stored = getattr(self.app, "_dest_index", {}).get("home")
        if isinstance(stored, int) and 0 <= stored < len(DESTINATIONS):
            self._index = stored
        nav_dest = getattr(self.app, "_nav_dest", None)
        self._refresh_header()
        self._refresh_graph()
        self._apply_narrow(self.size.width)
        if nav_dest:
            self.enter_destination(nav_dest)
        else:
            self._set_mode(drilled=False)
            self._apply_menu_selection()
            self.query_one("#home-dests").focus()

    def on_resize(self, event: Resize) -> None:
        self._apply_narrow(event.size.width)
        self._refresh_graph()

    def _apply_narrow(self, width: int) -> None:
        self.set_class(width < 80, "-narrow")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        dest = self._dest
        if action.startswith("jump") and isinstance(self.focused, Input):
            return False
        if action == "focus_filter":
            return dest == "search"
        if action == "cycle_filter":
            return dest in ("search", "recent")
        if action == "collect":
            return dest == "search" and not isinstance(self.focused, Input)
        if action == "remove":
            return dest in ("workspace", "queries")
        if action == "create_query":
            return dest in ("workspace", "queries")
        if action == "leave_filter":
            return isinstance(self.focused, Input)
        if action in ("page_down", "page_up"):
            return True
        return True

    def footer_action_keys(self) -> list[str]:
        dest = self._dest
        if dest is None:
            return [
                "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]navigate[/]",
                "[bold #e0e0e0]↵[/] [#888]open[/]",
                "[bold #e0e0e0]1[/]–[bold #e0e0e0]5[/] [#888]jump[/]",
            ]
        keys = [
            "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]navigate[/]",
            "[bold #e0e0e0]↵[/] [#888]open[/]",
        ]
        if dest == "search":
            keys = [
                "[bold #e0e0e0]/[/] [#888]search[/]",
                "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]select[/]",
                "[bold #e0e0e0]↵[/] [#888]open[/]",
                "[bold #e0e0e0]space[/] [#888]collect[/]",
                "[bold #e0e0e0]f[/] [#888]filter[/]",
            ]
        elif dest == "workspace":
            keys.extend(
                [
                    "[bold #e0e0e0]backspace[/] [#888]remove[/]",
                    "[bold #e0e0e0]c[/] [#888]query[/]",
                ]
            )
        elif dest == "queries":
            keys.extend(
                [
                    "[bold #e0e0e0]n[/] [#888]from workspace[/]",
                    "[bold #e0e0e0]backspace[/] [#888]delete[/]",
                ]
            )
        elif dest == "recent":
            keys.append("[bold #e0e0e0]f[/] [#888]filter[/]")
        keys.append("[bold #e0e0e0]esc[/] [#888]back[/]")
        return keys

    def footer_status(self) -> list[str]:
        return []

    def refresh_footer(self) -> None:
        parts = [*self.footer_action_keys(), *self.FOOTER_CHROME]
        self.query_one("#help-hint", Label).update("    ".join(parts))

    def _investigation(self) -> Investigation:
        inv = getattr(self.app, "investigation", None)
        if isinstance(inv, Investigation):
            return inv
        return Investigation.new("untitled", title=self.home_title)

    def _set_mode(self, *, drilled: bool) -> None:
        self.set_class(drilled, "-drilled")
        self.set_class(not drilled, "-menu")
        self.set_class(drilled and self._dest == "search", "-search")
        dests = self.query_one("#home-dests")
        lst = self.query_one("#home-list", ListView)
        inp = self.query_one("#search-input", Input)
        filt = self.query_one("#search-filters", Label)
        dests.display = not drilled
        lst.display = drilled
        searching = bool(drilled and self._dest == "search")
        inp.display = searching
        filt.display = searching
        if searching:
            self._refresh_search_filters()

    def _chrome_ready(self) -> bool:
        """False while the screen exists but compose has not attached widgets."""
        if not self.is_mounted:
            return False
        try:
            self.query_one("#home-title", Label)
            self.query_one("#home-graph-kicker", Label)
            return True
        except NoMatches:
            return False

    def _refresh_header(self) -> None:
        if not self._chrome_ready():
            return
        inv = self._investigation()
        self.query_one("#home-title", Label).update(inv.title or self.home_title)
        subtitle = self.query_one("#home-subtitle", Label)
        notes = (inv.notes or "").strip()
        subtitle.update(notes)
        subtitle.set_class(not notes, "-empty")

    def _refresh_search_filters(self) -> None:
        rtype = SEARCH_TYPES[self._type_i].upper()
        self.query_one("#search-filters", Label).update(
            f"TYPE: {rtype}  ·  SOURCE: ALL  ·  FREQUENCY: ALL"
        )

    def _refresh_graph(self) -> None:
        if not self._chrome_ready():
            return
        inv = self._investigation()
        n = len(inv.actions)
        kicker = self.query_one("#home-graph-kicker", Label)
        kicker.update(f"INVESTIGATION TREE  ·  {n} NODE{'S' if n != 1 else ''}")
        graph = self.query_one("#home-graph", Static)
        width = max(graph.size.width or 32, 16)
        height = max(graph.size.height or 6, 4)
        active = getattr(getattr(self.app, "active", None), "action", None)
        graph.update(
            render_action_graph(
                inv, width=width, height=height, active_id=active
            )
        )

    def refresh_live(self) -> None:
        """Host calls this after workspace/label mutations."""
        if not self._chrome_ready():
            return
        self._refresh_header()
        self._refresh_graph()
        if self._dest == "workspace":
            self._rebuild_list()
        elif self._dest is None:
            self._apply_menu_selection()
        self.refresh_footer()

    def enter_destination(self, dest: str) -> None:
        self._dest = dest
        self._set_mode(drilled=True)
        self._refresh_header()
        self._rebuild_list()
        self._refresh_graph()
        self.refresh_footer()
        if dest == "search":
            self.query_one("#search-input", Input).focus()
        else:
            self.query_one("#home-list", ListView).focus()

    def leave_destination(self) -> None:
        self._dest = None
        self._set_mode(drilled=False)
        self._refresh_header()
        self._apply_menu_selection()
        self._refresh_graph()
        self.refresh_footer()
        self.query_one("#home-dests").focus()

    def _apply_menu_selection(self) -> None:
        cards = list(self.query(DestinationCard))
        for i, card in enumerate(cards):
            card.set_class(i == self._index, "-selected")
        dest = DESTINATIONS[self._index]
        self.query_one("#home-data-kicker", Label).update(dest.title.upper())
        self.query_one("#home-data-body", Static).update(
            inspector_copy(dest, self._investigation())
        )
        remember = getattr(self.app, "remember_dest_index", None)
        if callable(remember):
            remember("home", self._index)

    def _rebuild_list(self) -> None:
        dest = self._dest
        list_view = self.query_one("#home-list", ListView)
        list_view.clear()
        if dest == "recent":
            for key in RECENT_FILTERS:
                list_view.append(FilterRow(key, key.upper()))
            stored = getattr(self.app, "_dest_index", {}).get("recent", 0)
            if isinstance(stored, int) and list_view.children:
                list_view.index = min(stored, len(RECENT_FILTERS) - 1)
        elif dest == "search":
            self._show_search_status("Type to search the catalog")
        elif dest == "actions":
            inv = self._investigation()
            rows = action_tree_rows(inv)
            height = tree_row_height(len(rows))
            for row in rows:
                list_view.append(ActionTreeItem(row, height))
            ids = [r.action_id for r in rows]
            active = getattr(getattr(self.app, "active", None), "action", None)
            if active in ids:
                list_view.index = ids.index(active)
        elif dest == "workspace":
            self._fill_workspace(list_view)
        elif dest == "queries":
            inv = self._investigation()
            list_view.append(QueryListItem("create"))
            for qid in sorted(inv.queries):
                list_view.append(QueryListItem("query", inv.queries[qid]))
        self._refresh_data_pane()

    def _fill_workspace(self, list_view: ListView) -> None:
        ws = self.workspace
        labels = getattr(self.app, "labels", {})
        if ws.taxonomy:
            list_view.append(GroupHeader("TAXONOMY"))
            list_view.append(
                WorkspaceResourceRow(
                    ws.taxonomy, "taxonomy", label_for(labels, ws.taxonomy)
                )
            )
        groups = [
            ("METRICS", "metric", list(ws.metric)),
            ("ENTITIES", "entity", list(ws.entity)),
            ("SERIES", "series", list(ws.series)),
        ]
        any_ids = False
        for title, rtype, ids in groups:
            list_view.append(GroupHeader(f"{title} · {len(ids)}"))
            for rid in ids:
                any_ids = True
                list_view.append(
                    WorkspaceResourceRow(rid, rtype, label_for(labels, rid))
                )
        if not any_ids and not ws.taxonomy:
            list_view.append(GroupHeader("Empty — Search and collect first"))

    def _list(self) -> ListView:
        return self.query_one("#home-list", ListView)

    def _highlighted(self):
        return self._list().highlighted_child

    def _refresh_data_pane(self) -> None:
        dest = self._dest
        kicker = self.query_one("#home-data-kicker", Label)
        body = self.query_one("#home-data-body", Static)
        if dest is None:
            self._apply_menu_selection()
            return
        child = self._highlighted()
        if dest == "recent":
            kicker.update("RECENT")
            filt = getattr(child, "key", RECENT_FILTERS[0]) if child else "all"
            labels = {
                "all": "All updates",
                "relevant": "Relevant to investigation",
                "filings": "SEC filings",
                "economic": "Economic releases",
            }
            body.update(
                "Latest filings and economic data updates.\n\n"
                f"[bold #888]FILTER[/]  {labels.get(filt, filt)}\n\n"
                "[#888]No recent updates available[/]"
            )
            return
        if dest == "search":
            if isinstance(child, SearchResultRow):
                kicker.update(f"{child.resource_type.upper()} / INSPECTOR")
                body.update(
                    resource_inspector_copy(child.resource, child.resource_type)
                )
            else:
                kicker.update("SEARCH")
                body.update(
                    "[#888]Highlight a result to inspect it here.[/]\n\n"
                    "Type two or more characters. f cycles resource type."
                )
            return
        if dest == "actions" and isinstance(child, ActionTreeItem):
            row = child.row
            action = self._investigation().get_action(row.action_id)
            kicker.update(row.description.upper()[:40])
            args = action.args or {}
            arg_s = ", ".join(f"{k}={v!r}" for k, v in args.items()) or "(none)"
            body.update(
                f"{row.type}  ·  id {row.action_id}\n"
                f"{row.child_count} child"
                f"{'' if row.child_count == 1 else 'ren'}\n\n"
                f"Args  {arg_s}\n\n"
                "Enter opens this view."
            )
            return
        if dest == "workspace" and isinstance(child, WorkspaceResourceRow):
            kicker.update(child.display_name.upper()[:40])
            body.update(
                f"{child.resource_type.upper()}  [{child.resource_id}]\n\n"
                "Enter opens a view.\n"
                "Backspace removes it from the bag.\n"
                "c snapshots the whole bag as a saved query."
            )
            return
        if dest == "queries":
            if isinstance(child, QueryListItem) and child.kind == "query" and child.query:
                q = child.query
                kicker.update((q.name or f"Q{q.id}").upper()[:40])
                body.update(
                    f"Q{q.id}  ·  saved\n\n"
                    f"{len(q.metric)} metrics · {len(q.entity)} entities · "
                    f"{len(q.series)} series\n"
                    f"Frequency: {q.frequency or 'Any'}  ·  "
                    f"Source: {q.source or 'Any'}\n"
                    f"Units: {q.units or 'Native'}\n\n"
                    "Enter opens JSON results.\n"
                    "Backspace deletes. No edit in this pass."
                )
            else:
                ws = self.workspace
                kicker.update("NEW QUERY")
                body.update(
                    "Create by copying the current workspace bag.\n\n"
                    + (
                        "Workspace is empty — Search and collect first."
                        if ws.is_empty()
                        else f"Bag {workspace_counts_line(self._investigation())} "
                        "will be copied. Later bag edits do not change this query."
                    )
                )
            return
        kicker.update(dest.upper())
        body.update("")

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._dest is None:
            self._index = min(self._index + 1, len(DESTINATIONS) - 1)
            self._apply_menu_selection()
            dests = self.query_one("#home-dests")
            cards = list(self.query(DestinationCard))
            if self._index < len(cards):
                dests.scroll_to_widget(cards[self._index], animate=False)
            return
        lv = self._list()
        lv.focus()
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()
        if self._dest == "search" and isinstance(self._highlighted(), GroupHeader):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._dest is None:
            self._index = max(self._index - 1, 0)
            self._apply_menu_selection()
            dests = self.query_one("#home-dests")
            cards = list(self.query(DestinationCard))
            if self._index < len(cards):
                dests.scroll_to_widget(cards[self._index], animate=False)
            return
        lv = self._list()
        lv.focus()
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()
        if self._dest == "search" and isinstance(self._highlighted(), GroupHeader):
            lv.action_cursor_up()

    def action_page_down(self) -> None:
        self._page(+1)

    def action_page_up(self) -> None:
        self._page(-1)

    def _page(self, direction: int) -> None:
        if isinstance(self.focused, Input):
            return
        if self._dest is None:
            dests = self.query_one("#home-dests")
            if direction > 0:
                dests.scroll_page_down()
            else:
                dests.scroll_page_up()
            return
        lv = self._list()
        lv.focus()
        if direction > 0:
            lv.action_page_down()
        else:
            lv.action_page_up()

    def action_leave(self) -> None:
        if isinstance(self.focused, Input):
            self._list().focus()
            return
        if self._dest:
            leave = getattr(self.app, "leave_destination", None)
            if callable(leave):
                leave()
            else:
                self.leave_destination()
            return
        self.app.bell()

    def action_leave_filter(self) -> None:
        if isinstance(self.focused, Input):
            self._list().focus()

    def action_focus_filter(self) -> None:
        if self._dest == "search":
            self.query_one("#search-input", Input).focus()

    def action_cycle_filter(self) -> None:
        if self._dest == "search":
            self._type_i = (self._type_i + 1) % len(SEARCH_TYPES)
            self._refresh_search_filters()
            self._kick_search()
            self._refresh_data_pane()
            return
        if self._dest == "recent":
            self.action_cursor_down()

    def _jump(self, index: int) -> None:
        self._index = index
        self._apply_menu_selection()
        self.action_confirm()

    def action_jump_1(self) -> None:
        self._jump(0)

    def action_jump_2(self) -> None:
        self._jump(1)

    def action_jump_3(self) -> None:
        self._jump(2)

    def action_jump_4(self) -> None:
        self._jump(3)

    def action_jump_5(self) -> None:
        self._jump(4)

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self._list().focus()
            return
        if self._dest is None:
            dest = DESTINATIONS[self._index]
            open_fn = getattr(self.app, "open_destination", None)
            if open_fn is None:
                self.enter_destination(dest.id)
                return
            open_fn(dest.id)
            return
        child = self._highlighted()
        if self._dest == "recent":
            self.notify("Recent feed is not available yet", severity="warning")
            return
        if self._dest == "search" and isinstance(child, SearchResultRow):
            open_fn = getattr(self.app, "open_resource_view", None)
            if callable(open_fn):
                open_fn(
                    child.resource_type,
                    child.resource.id,
                    label=_resource_label(child.resource),
                )
            return
        if self._dest == "actions" and isinstance(child, ActionTreeItem):
            open_fn = getattr(self.app, "open_action_from_nav", None)
            if callable(open_fn):
                open_fn(child.row.action_id)
            return
        if self._dest == "workspace" and isinstance(child, WorkspaceResourceRow):
            open_fn = getattr(self.app, "open_resource_view", None)
            if callable(open_fn):
                open_fn(
                    child.resource_type,
                    child.resource_id,
                    label=child.display_name,
                )
            return
        if self._dest == "queries" and isinstance(child, QueryListItem):
            if child.kind == "create":
                self.action_create_query()
                return
            if child.query is not None:
                open_fn = getattr(self.app, "open_query_view", None)
                if callable(open_fn):
                    open_fn(child.query.id)

    def action_collect(self) -> None:
        child = self._highlighted()
        if not isinstance(child, SearchResultRow):
            return
        resource = child.resource
        rtype = child.resource_type
        if isinstance(resource, Taxonomy) or rtype == "taxonomy":
            self.workspace.taxonomy = resource.id
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(resource.id, _resource_label(resource))
            persist = getattr(self.app, "persist_active_workspace", None)
            if callable(persist):
                persist()
            self.notify(f"Taxonomy set to {resource.id}")
            return
        kwargs: dict[str, Any] = {"labels": {resource.id: _resource_label(resource)}}
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
        self._refresh_header()
        self._refresh_graph()

    def action_remove(self) -> None:
        child = self._highlighted()
        if self._dest == "workspace" and isinstance(child, WorkspaceResourceRow):
            rid = child.resource_id
            if child.resource_type == "taxonomy":
                self.workspace.taxonomy = None
            else:
                self.workspace.remove_id(rid)
            labels = getattr(self.app, "labels", None)
            if isinstance(labels, dict):
                labels.pop(rid, None)
            persist = getattr(self.app, "persist_active_workspace", None)
            if callable(persist):
                persist()
            self.notify(f"Removed {rid}")
            self._rebuild_list()
            self._refresh_header()
            self._list().focus()
            return
        if self._dest == "queries" and isinstance(child, QueryListItem):
            if child.kind != "query" or child.query is None:
                return
            delete = getattr(self.app, "delete_query", None)
            if callable(delete):
                delete(child.query.id)

    def action_create_query(self) -> None:
        create = getattr(self.app, "query_from_workspace", None)
        if callable(create):
            create()

    def _kick_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if self._search_task:
            self._search_task.cancel()
        if len(query) < 2:
            msg = (
                "Type to search the catalog"
                if not query
                else "Type two or more characters"
            )
            self._show_search_status(msg)
            return
        self._show_search_status("Searching…")
        self._search_task = asyncio.create_task(self._run_search(query))

    def _show_search_status(self, status: str) -> None:
        list_view = self._list()
        list_view.clear()
        list_view.append(GroupHeader(status))
        self._refresh_data_pane()

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
            self._show_search_status(f"Search error: {exc}")
            self.notify(f"Search error: {exc}", severity="error")
            return
        list_view = self._list()
        list_view.clear()
        if not rows:
            list_view.append(GroupHeader(f"No matches for {query!r}"))
        else:
            first_row = None
            prefer_entity = False
            for kind, payload, rtype in clustered_search_items(rows):
                if kind == "header":
                    title = str(payload)
                    prefer_entity = title.upper() not in TYPE_HEADINGS
                    list_view.append(GroupHeader(title))
                elif payload is not None and rtype is not None:
                    list_view.append(
                        SearchResultRow(
                            payload,
                            rtype,
                            prefer_entity=prefer_entity and rtype == "series",
                        )
                    )
                    if first_row is None:
                        first_row = len(list_view.children) - 1
            if first_row is not None:
                list_view.index = first_row
        self._refresh_data_pane()

    @on(Input.Changed, "#search-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        if self._dest == "search":
            self._kick_search()

    @on(Input.Submitted, "#search-input")
    def on_search_submit(self) -> None:
        self._list().focus()

    @on(ListView.Highlighted, "#home-list")
    def on_list_highlighted(self, event: ListView.Highlighted) -> None:
        idx = self._list().index or 0
        remember = getattr(self.app, "remember_dest_index", None)
        if callable(remember) and self._dest:
            remember(self._dest, idx)
        self._refresh_data_pane()

    @on(ListView.Selected, "#home-list")
    def on_list_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()


def create_home_screen(client, workspace, **kwargs):
    """Factory used by InvestigationHost for ``type: home`` actions."""
    title = kwargs.pop("title", "Investigation")
    body = kwargs.pop("body", "")
    kwargs.pop("meta", None)
    kwargs.pop("input_context", None)
    return HomeScreen(
        client,
        workspace,
        title=title,
        body=body,
    )


HOME = register(
    ActionSpec(
        id="home",
        name="Home",
        description=(
            "Investigation shell: destinations on the left, data on the right, "
            "action tree along the bottom. g always returns here."
        ),
        create_screen=create_home_screen,
        arguments=(),
        bindings=(
            ActionBinding("j / k", "cursor", "Move in the current options list"),
            ActionBinding("enter", "confirm", "Open the highlighted option"),
            ActionBinding("1–5", "jump", "Jump to Recent / Search / Actions / Workspace / Queries"),
            ActionBinding("esc", "back", "Previous navigation level"),
            ActionBinding("g", "go_home", "Return to home"),
        ),
        example="jst open tutorial",
    )
)
