"""Home — persistent three-pane investigation shell."""

from __future__ import annotations

from rich.markup import escape

import asyncio
import webbrowser
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Resize
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..actions.base import ActionBinding, ActionSpec, label_for, register, stage_ids
from ..actions.pagination import page_fetch_limit, take_page
from ..actions.screen import BaseScreen
from ..client import JSTDataClient
from ..investigation.model import Investigation, Workspace
from ..models import Entity, Event, Metric, Release, Series, Taxonomy
from .browsers import (
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
    annotations_panel,
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

RECENT_PAGE = 20

# EDGAR timestamps are UTC; filers and readers think in New York time.
SEC_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class RecentFilter:
    """One Recent filter chip: an 8-K item preset, or the plain release list."""

    label: str
    items: tuple[str, ...] = ()
    releases: bool = False


RECENT_FILTERS: tuple[RecentFilter, ...] = (
    RecentFilter("8-K ALL"),
    RecentFilter("EARNINGS", ("2.02",)),
    RecentFilter("LEADERSHIP", ("5.02",)),
    RecentFilter("DEALS", ("1.01", "1.02", "2.01")),
    RecentFilter("DISTRESS", ("1.03", "2.04", "2.06", "3.01", "4.02")),
    RecentFilter("AUDITOR", ("4.01", "4.02")),
    RecentFilter("ALL RELEASES", releases=True),
)


class DestinationCard(Static):
    """One of the five Home destinations — large, numbered, selectable."""

    def __init__(self, dest: Destination) -> None:
        super().__init__()
        self.dest = dest

    def compose(self) -> ComposeResult:
        yield Label(self.dest.number, classes="dest-num")
        yield Label(self.dest.title, classes="dest-title")
        yield Label(self.dest.blurb, classes="dest-blurb")


class ReleaseTile(ListItem):
    """One stacked Recent card: title, id, date · source."""

    def __init__(self, release: Release) -> None:
        super().__init__()
        self.release = release

    def compose(self) -> ComposeResult:
        rel = self.release
        yield Label(rel.label or rel.id, classes="rel-title")
        yield Label(rel.id, classes="rel-id")
        source = rel.source or "—"
        yield Label(f"{rel.release_date.isoformat()}  ·  {source}", classes="rel-meta")


def event_time(event: Event) -> str:
    """Acceptance time in New York time, e.g. ``2026-11-17 16:41 ET``."""
    accepted = event.accepted_at
    if accepted.tzinfo is None:
        accepted = accepted.replace(tzinfo=timezone.utc)
    return accepted.astimezone(SEC_TZ).strftime("%Y-%m-%d %H:%M ET")


def event_company(event: Event) -> str:
    tickers = " ".join(event.tickers[:3])
    return f"{event.entity_label}  {tickers}" if tickers else event.entity_label


class EventTile(ListItem):
    """One stacked Recent card for an 8-K: company, headline, time · form · items."""

    def __init__(self, event: Event) -> None:
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        ev = self.event
        yield Label(event_company(ev), classes="rel-title")
        yield Label(ev.headline or "—", classes="evt-headline")
        form = f"{ev.form} amendment" if ev.is_amendment else ev.form
        codes = " ".join(ev.item_codes)
        yield Label(f"{event_time(ev)}  ·  {form}  ·  {codes}", classes="rel-meta")


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
        Binding("o", "open_filing", "Open filing on sec.gov", show=False),
        Binding("backspace", "remove", "Remove", show=False),
        Binding("delete", "remove", "Remove", show=False),
        Binding("c", "create_query", "Query from workspace", show=False),
        Binding("n", "create_query", "Query from workspace", show=False),
        Binding("a", "annotate", "Annotate", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
    ]

    FOOTER_CHROME: tuple[str, ...] = (
        "[bold #e0e0e0]:[/] [#888]commands[/]",
        "[bold #e0e0e0]{[/]/[bold #e0e0e0]}[/] [#888]tabs[/]",
        "[bold #e0e0e0]t[/] [#888]tutorials[/]",
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
    SearchResultRow, WorkspaceResourceRow, QueryListItem, ActionTreeItem {
        width: 100%;
        padding: 0 1;
    }
    SearchResultRow {
        border-left: solid #0a0a0a;
        padding: 0 1 0 0;
    }
    SearchResultRow, GroupHeader {
        height: 1;
        min-height: 1;
        max-height: 1;
    }
    WorkspaceResourceRow {
        height: 4;
        min-height: 4;
        max-height: 4;
        padding: 1 1;
    }
    ReleaseTile, EventTile {
        height: 5;
        min-height: 5;
        max-height: 5;
        width: 100%;
        /* Border takes 2 of the 5 rows: no top padding, or the third line is clipped. */
        padding: 0 1;
        margin: 0 0 1 0;
        border: solid #222;
        background: #111;
    }
    .rel-title { color: #e0e0e0; text-style: bold; height: 1; }
    .rel-id { color: #888; height: 1; }
    .rel-meta { color: #86efac; height: 1; }
    .evt-headline { color: #c8c8c8; height: 1; }
    QueryListItem { height: 2; min-height: 2; }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    ListView > ReleaseTile.--highlight, ListView > EventTile.--highlight {
        background: #1a3a2a;
        border: solid #4ade80;
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
        self._recent_i = 0
        # Release rows under ALL RELEASES, Event rows under the 8-K filters.
        self._releases: list[Release | Event] = []
        self._recent_offset = 0
        self._recent_has_more = False
        self._recent_loading_more = False
        self._recent_loaded = False

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
            return dest in ("search", "recent") and not isinstance(self.focused, Input)
        if action == "open_filing":
            return dest == "recent"
        if action == "remove":
            return dest in ("workspace", "queries")
        if action == "create_query":
            return dest in ("workspace", "queries")
        if action == "annotate":
            if isinstance(self.focused, Input):
                return False
            if dest is None:
                return True
            if dest == "queries":
                child = self._highlighted()
                return (
                    isinstance(child, QueryListItem)
                    and child.kind == "query"
                    and child.query is not None
                )
            return False
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
                "[bold #e0e0e0]a[/] [#888]annotate[/]",
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
        elif dest == "recent" and not self._recent_filter().releases:
            keys = [
                "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]navigate[/]",
                "[bold #e0e0e0]↵[/] [#888]company[/]",
                "[bold #e0e0e0]o[/] [#888]filing[/]",
                "[bold #e0e0e0]space[/] [#888]collect[/]",
                "[bold #e0e0e0]f[/] [#888]filter[/]",
            ]
        elif dest == "recent":
            keys.append("[bold #e0e0e0]f[/] [#888]filter[/]")
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
                    "[bold #e0e0e0]a[/] [#888]annotate[/]",
                    "[bold #e0e0e0]backspace[/] [#888]delete[/]",
                ]
            )
        keys.append("[bold #e0e0e0]esc[/] [#888]back[/]")
        return keys

    def footer_status(self) -> list[str]:
        tab_fn = getattr(self.app, "investigation_tab_chips", None)
        if callable(tab_fn):
            chips = tab_fn()
            if chips:
                return [" ".join(chips)]
        return []

    def refresh_footer(self) -> None:
        parts = [
            *self.footer_status(),
            *self.footer_action_keys(),
            *self.FOOTER_CHROME,
        ]
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
        filt.display = searching or bool(drilled and self._dest == "recent")
        if searching:
            self._refresh_search_filters()
        elif drilled and self._dest == "recent":
            self._refresh_recent_filters()

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
        # Title and notes are user (or cloned) text, not markup.
        self.query_one("#home-title", Label).update(
            escape(inv.title) if inv.title else self.home_title
        )
        subtitle = self.query_one("#home-subtitle", Label)
        notes = (inv.notes or "").strip()
        subtitle.update(escape(notes))
        subtitle.set_class(not notes, "-empty")

    def _refresh_search_filters(self) -> None:
        rtype = SEARCH_TYPES[self._type_i].upper()
        self.query_one("#search-filters", Label).update(
            f"TYPE: {rtype}  ·  SOURCE: ALL  ·  FREQUENCY: ALL"
        )

    def _recent_filter(self) -> RecentFilter:
        return RECENT_FILTERS[self._recent_i]

    def _refresh_recent_filters(self) -> None:
        # One chip, not all seven: the nav pane is ~40% of the terminal.
        filt = self._recent_filter()
        if filt.releases:
            scope = "FRED, EDGAR, BEA…"
        elif filt.items:
            scope = "items " + " ".join(filt.items)
        else:
            scope = "every item"
        self.query_one("#search-filters", Label).update(
            f"[bold #4ade80]{filt.label}[/]  {scope}  "
            f"[#555]{self._recent_i + 1}/{len(RECENT_FILTERS)} · f next[/]"
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
        """Host calls this after workspace/label/annotation mutations."""
        if not self._chrome_ready():
            return
        self._refresh_header()
        self._refresh_graph()
        if self._dest == "workspace":
            self._rebuild_list()
        elif self._dest == "queries":
            self._rebuild_list()
        elif self._dest is None:
            self._apply_menu_selection()
        elif self._dest is not None:
            self._refresh_data_pane()
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
            what = "releases" if self._recent_filter().releases else "8-K events"
            list_view.append(GroupHeader(f"Loading recent {what}…"))
            self.load_recent(reset=True)
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
            # Highlight a row so Enter acts ("Create from workspace" first).
            stored = getattr(self.app, "_dest_index", {}).get("queries", 0)
            count = 1 + len(inv.queries)
            list_view.index = stored if isinstance(stored, int) and stored < count else 0
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
        # Count appends ourselves (children lag pending mounts) so the first
        # resource row can be highlighted; otherwise Enter / Backspace no-op.
        appended = 2 if ws.taxonomy else 0  # list was just cleared
        first_row = 1 if ws.taxonomy else None
        for title, rtype, ids in groups:
            list_view.append(GroupHeader(f"{title} · {len(ids)}"))
            appended += 1
            for rid in ids:
                any_ids = True
                list_view.append(
                    WorkspaceResourceRow(rid, rtype, label_for(labels, rid))
                )
                if first_row is None:
                    first_row = appended
                appended += 1
        if not any_ids and not ws.taxonomy:
            list_view.append(GroupHeader("Empty — Search and collect first"))
        if first_row is not None:
            list_view.index = first_row

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
            extra = "+" if self._recent_has_more else ""
            noun = "releases" if self._recent_filter().releases else "8-K events"
            if isinstance(child, EventTile):
                ev = child.event
                kicker.update(f"8-K / {ev.entity_label.upper()[:34]}")
                body.update(self._event_inspector(ev, f"{len(self._releases)}{extra}"))
            elif isinstance(child, ReleaseTile):
                rel = child.release
                kicker.update((rel.label or rel.id).upper()[:40])
                body.update(
                    f"[bold]{rel.label or rel.id}[/bold]\n\n"
                    f"id      {rel.id}\n"
                    f"date    {rel.release_date.isoformat()}\n"
                    f"source  {rel.source or '—'}\n\n"
                    f"{len(self._releases)}{extra} releases · newest first\n"
                    "Enter opens this release."
                )
            else:
                kicker.update("RECENT")
                if self._recent_loading_more:
                    body.update("Loading more…")
                elif not self._recent_loaded:
                    body.update(
                        "Latest 8-K material events and releases, newest first.\n\n"
                        "[#888]Loading…[/]"
                    )
                elif not self._releases:
                    body.update(f"[#888]No recent {noun}.[/]")
                else:
                    body.update(
                        f"{len(self._releases)}{extra} {noun} · newest first"
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
                # Prefer live object from the investigation (annotations may
                # have been edited since the list item was built).
                live = self._investigation().queries.get(q.id, q)
                kicker.update((live.name or f"Q{live.id}").upper()[:40])
                body.update(
                    f"Q{live.id}  ·  saved\n\n"
                    f"{len(live.metric)} metrics · {len(live.entity)} entities · "
                    f"{len(live.series)} series\n"
                    f"Frequency: {live.frequency or 'Any'}  ·  "
                    f"Source: {live.source or 'Any'}\n"
                    f"Units: {live.units or 'Native'}\n\n"
                    f"{annotations_panel(live.annotations)}\n\n"
                    "Enter opens JSON results.\n"
                    "a edits annotations · Backspace deletes."
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
        if self._dest in ("search", "recent") and isinstance(
            self._highlighted(), GroupHeader
        ):
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
        if self._dest in ("search", "recent") and isinstance(
            self._highlighted(), GroupHeader
        ):
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

    def _event_inspector(self, ev: Event, count: str) -> str:
        filt = self._recent_filter()
        lines = [f"[bold]{ev.entity_label}[/bold]"]
        if ev.tickers:
            lines.append(", ".join(ev.tickers))
        lines.append(f"[#888]{ev.entity_id}[/]")
        lines.append("")
        form = f"{ev.form}  [#f59e0b]amendment[/]" if ev.is_amendment else ev.form
        lines.append(f"form      {form}")
        lines.append(f"accepted  {event_time(ev)}")
        if ev.report_date:
            lines.append(f"event     {ev.report_date.isoformat()}")
        lines.append(f"accession {ev.accession_number}")
        lines.append("")
        lines.append("[bold #4ade80]ITEMS[/]")
        for item in ev.reported_items:
            lines.append(f"{item.code:<5} {item.title}")
        lines.append("")
        lines.append(f"[#888]{ev.url}[/]")
        lines.append("")
        scope = "all items" if not filt.items else "items " + ", ".join(filt.items)
        lines.append(f"[#888]{count} events · {scope} · newest first[/]")
        lines.append("Enter opens the company · o opens the filing · space collects")
        return "\n".join(lines)

    def action_open_filing(self) -> None:
        child = self._highlighted()
        if self._dest != "recent" or not isinstance(child, EventTile):
            return
        webbrowser.open(child.event.url)
        self.notify(f"Opened {child.event.accession_number} on sec.gov")

    def action_cycle_filter(self) -> None:
        if self._dest == "recent":
            self._recent_i = (self._recent_i + 1) % len(RECENT_FILTERS)
            self._refresh_recent_filters()
            self._rebuild_list()
            self.refresh_footer()
            return
        if self._dest == "search":
            self._type_i = (self._type_i + 1) % len(SEARCH_TYPES)
            self._refresh_search_filters()
            self._kick_search()
            self._refresh_data_pane()
            return

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
            if isinstance(child, EventTile):
                open_fn = getattr(self.app, "open_resource_view", None)
                if callable(open_fn):
                    ev = child.event
                    open_fn("entity", ev.entity_id, label=ev.entity_label)
                return
            if isinstance(child, ReleaseTile):
                open_fn = getattr(self.app, "open_resource_view", None)
                if callable(open_fn):
                    rel = child.release
                    open_fn(
                        "release",
                        rel.id,
                        label=rel.label or rel.id,
                    )
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
        if isinstance(child, EventTile):
            ev = child.event
            added = stage_ids(
                self.workspace,
                self.app,
                entity=ev.entity_id,
                labels={ev.entity_id: ev.entity_label},
            )
            if not added:
                self.notify(f"{ev.entity_label} already in workspace", severity="warning")
                return
            self.notify(f"Collected {ev.entity_label} ({ev.entity_id})")
            self._refresh_header()
            self._refresh_graph()
            return
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

    def action_annotate(self) -> None:
        if self._dest is None:
            edit = getattr(self.app, "edit_investigation_annotations", None)
            if callable(edit):
                edit()
            return
        if self._dest == "queries":
            child = self._highlighted()
            if (
                isinstance(child, QueryListItem)
                and child.kind == "query"
                and child.query is not None
            ):
                edit = getattr(self.app, "edit_query_annotations", None)
                if callable(edit):
                    edit(child.query.id)

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
            # Count appends ourselves: ``list_view.children`` lags pending mounts,
            # which put the default highlight on the second result.
            appended = 0
            for kind, payload, rtype in clustered_search_items(rows):
                if kind == "header":
                    title = str(payload)
                    prefer_entity = title.upper() not in TYPE_HEADINGS
                    list_view.append(GroupHeader(title))
                    appended += 1
                elif payload is not None and rtype is not None:
                    list_view.append(
                        SearchResultRow(
                            payload,
                            rtype,
                            prefer_entity=prefer_entity and rtype == "series",
                        )
                    )
                    appended += 1
                    if first_row is None:
                        first_row = appended - 1
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
        if self._dest == "recent":
            self._maybe_load_more_recent()
        self._refresh_data_pane()

    @on(ListView.Selected, "#home-list")
    def on_list_selected(self, event: ListView.Selected) -> None:
        self.action_confirm()

    def _maybe_load_more_recent(self) -> None:
        if self._recent_loading_more or not self._recent_has_more:
            return
        if not isinstance(self._highlighted(), (ReleaseTile, EventTile)):
            return
        index = self._list().index
        if index is None or index < len(self._releases) - 1:
            return
        self._recent_loading_more = True
        self._recent_offset = len(self._releases)
        self.load_recent(reset=False)

    def _append_releases(self, releases: list[Release | Event]) -> None:
        list_view = self._list()
        remember = getattr(self.app, "remember_label", None)
        for rel in releases:
            if isinstance(rel, Event):
                if callable(remember):
                    remember(rel.entity_id, rel.entity_label)
                list_view.append(EventTile(rel))
                continue
            if callable(remember):
                remember(rel.id, rel.label or rel.id)
            list_view.append(ReleaseTile(rel))

    @work(exclusive=True, group="home-recent")
    async def load_recent(self, reset: bool = True) -> None:
        if reset:
            self._recent_offset = 0
            self._recent_has_more = False
            self._recent_loading_more = False
            self._recent_loaded = False
        offset = self._recent_offset
        filt = self._recent_filter()
        try:
            if filt.releases:
                raw = await asyncio.to_thread(
                    self.client.list_releases,
                    page_fetch_limit(RECENT_PAGE),
                    offset,
                    "desc",
                )
            else:
                raw = await asyncio.to_thread(
                    self.client.list_events,
                    page_fetch_limit(RECENT_PAGE),
                    offset,
                    list(filt.items) or None,
                )
            page, has_more = take_page(raw, RECENT_PAGE)
        except Exception as exc:
            if self._dest != "recent" or filt != self._recent_filter():
                return
            self._recent_loading_more = False
            self._recent_loaded = True
            self._list().clear()
            what = "releases" if filt.releases else "8-K events"
            self._list().append(GroupHeader(f"Could not load {what}: {exc}"))
            self._refresh_data_pane()
            return
        # A filter change mid-flight starts its own load; drop this stale page.
        if self._dest != "recent" or filt != self._recent_filter():
            return
        if reset:
            self._releases = []
            self._list().clear()
        self._releases.extend(page)
        self._recent_has_more = has_more
        self._recent_loading_more = False
        self._recent_loaded = True
        if reset and not page:
            what = "releases" if filt.releases else "8-K events"
            self._list().append(GroupHeader(f"No recent {what}"))
        else:
            self._append_releases(page)
            if reset:
                # Highlight the newest row so the inspector shows it straight away.
                self._list().index = 0
        self._refresh_data_pane()
        self.refresh_footer()


def create_home_screen(client, workspace, **kwargs):
    """Factory used by InvestigationHost for ``type: home`` actions.

    Chart-first redesign: land on :class:`ResearchRootScreen` instead of the
    five-destination Home hub.
    """
    from .research_root import create_research_root_screen

    return create_research_root_screen(client, workspace, **kwargs)


HOME = register(
    ActionSpec(
        id="home",
        name="Overview",
        description=(
            "Investigation / report overview with saved queries, notes, and "
            "workspace summary. g / Home returns here; / search; n new query."
        ),
        create_screen=create_home_screen,
        arguments=(),
        bindings=(
            ActionBinding("/", "search", "Universal search"),
            ActionBinding("n", "new_query", "New draft query"),
            ActionBinding("H", "history", "Action history"),
            ActionBinding("F", "feedback", "Feedback"),
            ActionBinding("H", "go_home", "Return to Overview"),
        ),
        example="jst open",
    )
)
