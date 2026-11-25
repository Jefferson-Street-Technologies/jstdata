"""Shell chrome widgets for the chart-first research layout."""

from __future__ import annotations

from typing import Any, Literal, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, Static

JumpKind = Literal["queries", "workspace"]


class ProjectHeader(Static):
    """Top project identity strip."""

    DEFAULT_CSS = """
    ProjectHeader {
        height: 1;
        width: 100%;
        background: #0f0f0f;
        color: #e0e0e0;
        text-style: bold;
        padding: 0 1;
    }
    """

    def __init__(self, title: str = "JST", **kwargs: Any) -> None:
        super().__init__(title, **kwargs)

    def set_project(self, title: str, *, commands_hint: bool = True) -> None:
        hint = "   [: commands]" if commands_hint else ""
        self.update(f"JST / {title}{hint}")


class TabBar(Static):
    """Investigation tab strip (clickable labels via message on click regions)."""

    class TabSelected(Message):
        def __init__(self, investigation_id: str) -> None:
            self.investigation_id = investigation_id
            super().__init__()

    DEFAULT_CSS = """
    TabBar {
        height: 1;
        width: 100%;
        background: #0a0a0a;
        color: #888;
        padding: 0 1;
        border-bottom: solid #222;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)
        self._order: list[str] = []
        self._labels: dict[str, str] = {}
        self._active: Optional[str] = None
        self._marks: dict[str, str] = {}

    def set_tabs(
        self,
        order: list[str],
        *,
        labels: dict[str, str],
        active: Optional[str],
        marks: Optional[dict[str, str]] = None,
    ) -> None:
        self._order = list(order)
        self._labels = dict(labels)
        self._active = active
        self._marks = dict(marks or {})
        parts: list[str] = []
        for i, inv_id in enumerate(self._order, start=1):
            label = self._labels.get(inv_id, inv_id)
            mark = self._marks.get(inv_id, "")
            text = f"{i} {label}{mark}"
            if inv_id == active:
                parts.append(f"[bold #4ade80]\\[{text}][/]")
            else:
                parts.append(f"\\[{text}]")
        parts.append("[dim]\\[+][/]")
        self.update("  ".join(parts) if parts else "[dim](no investigations)[/]")


class _SidebarQueryRow(Static):
    """Clickable query row — draft is always the pinned slot; saved sit below."""

    def __init__(
        self,
        query_id: int,
        label: str,
        *,
        draft: bool,
        empty: bool = False,
        active: bool = False,
    ) -> None:
        super().__init__()
        self.query_id = query_id
        self.draft = draft
        self.empty = empty
        self.active = active
        self._label = label
        self.jump_index: Optional[int] = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-row"):
            yield Label(" ", classes="sidebar-jump-num", markup=True)
            with Vertical(classes="sidebar-q-body"):
                if self.draft:
                    yield Label("DRAFT", classes="sidebar-q-kicker")
                    if self.empty or not self._label:
                        yield Label(
                            "[dim]Empty · / search or n[/]",
                            classes="sidebar-q-title",
                            markup=True,
                        )
                    else:
                        yield Label(self._label, classes="sidebar-q-title")
                else:
                    yield Label(f"Q{self.query_id}", classes="sidebar-q-kicker")
                    yield Label(
                        self._label or f"Query {self.query_id}",
                        classes="sidebar-q-title",
                    )

    def on_mount(self) -> None:
        self.set_class(self.active, "-active")
        self._paint_jump(None)

    def set_jump(self, index: Optional[int]) -> None:
        self.jump_index = index
        self._paint_jump(index)

    def _paint_jump(self, index: Optional[int]) -> None:
        try:
            num = self.query_one(".sidebar-jump-num", Label)
        except Exception:  # noqa: BLE001
            return
        if index is None:
            num.update(" ")
            self.remove_class("-jump")
        else:
            num.update(f"[bold #fbbf24]{index}[/]")
            self.add_class("-jump")

    def on_click(self) -> None:
        parent = self.parent
        while parent is not None and not isinstance(parent, ResearchSidebar):
            parent = parent.parent
        if isinstance(parent, ResearchSidebar):
            parent.activate_query(self.query_id)


class _SidebarSectionHeader(Static):
    """Faint subheader between flat workspace resource groups."""

    def __init__(self, title: str) -> None:
        super().__init__(f"[dim]{title}[/]", markup=True, classes="sidebar-section")


class _SidebarResourceRow(Static):
    """Clickable flat workspace resource (entity / metric / series)."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        label: str,
    ) -> None:
        super().__init__()
        self.resource_type = resource_type
        self.resource_id = resource_id
        self._label = label or resource_id
        self.jump_index: Optional[int] = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-row"):
            yield Label(" ", classes="sidebar-jump-num", markup=True)
            yield Label(self._label, classes="sidebar-res-title")

    def on_mount(self) -> None:
        self._paint_jump(None)

    def set_jump(self, index: Optional[int]) -> None:
        self.jump_index = index
        self._paint_jump(index)

    def _paint_jump(self, index: Optional[int]) -> None:
        try:
            num = self.query_one(".sidebar-jump-num", Label)
        except Exception:  # noqa: BLE001
            return
        if index is None:
            num.update(" ")
            self.remove_class("-jump")
        else:
            num.update(f"[bold #fbbf24]{index}[/]")
            self.add_class("-jump")

    def on_click(self) -> None:
        parent = self.parent
        while parent is not None and not isinstance(parent, ResearchSidebar):
            parent = parent.parent
        if isinstance(parent, ResearchSidebar):
            parent.activate_resource(self.resource_type, self.resource_id)


class ResearchSidebar(Static):
    """Left sidebar: click to open; ``1``/``2`` then a digit to jump."""

    class QuerySelected(Message):
        def __init__(self, query_id: int) -> None:
            self.query_id = query_id
            super().__init__()

    class ResourceSelected(Message):
        def __init__(self, resource_type: str, resource_id: str) -> None:
            self.resource_type = resource_type
            self.resource_id = resource_id
            super().__init__()

    class NewQuery(Message):
        pass

    sidebar_collapsed: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    ResearchSidebar {
        width: 32;
        height: 100%;
        background: #0c0c0c;
        border-right: solid #222;
        padding: 0 0 0 0;
    }
    ResearchSidebar.-collapsed {
        width: 0;
        min-width: 0;
        border-right: none;
        display: none;
    }
    ResearchSidebar.-jump-queries #sidebar-queries-kicker,
    ResearchSidebar.-jump-workspace #sidebar-ws-kicker {
        color: #fbbf24;
        text-style: bold;
    }
    #sidebar-queries-kicker, #sidebar-ws-kicker {
        color: #a0a0a0;
        text-style: bold;
        height: 1;
        padding: 0 1;
        margin-top: 1;
    }
    #sidebar-new-query {
        color: #86efac;
        height: 1;
        padding: 0 1;
        margin-bottom: 1;
    }
    #sidebar-query-list {
        height: 1fr;
        max-height: 55%;
        background: #0c0c0c;
        padding: 0 0 0 0;
    }
    #sidebar-ws-list {
        height: 1fr;
        max-height: 45%;
        background: #0c0c0c;
        padding: 0 0 0 0;
    }
    ResearchSidebar.-ws-empty #sidebar-query-list {
        max-height: 75%;
    }
    ResearchSidebar.-ws-empty #sidebar-ws-list {
        max-height: 6;
        height: 6;
    }
    .sidebar-row {
        height: 100%;
        width: 100%;
    }
    .sidebar-jump-num {
        width: 3;
        height: 1;
        color: #fbbf24;
        text-style: bold;
        content-align: center middle;
    }
    _SidebarQueryRow {
        height: 3;
        min-height: 3;
        max-height: 3;
        width: 100%;
        padding: 0 1;
        border-left: solid #0c0c0c;
    }
    _SidebarQueryRow:hover, _SidebarQueryRow.-active {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    _SidebarQueryRow.-jump {
        background: #1a1a12;
        border-left: solid #fbbf24;
    }
    .sidebar-q-body {
        height: 3;
        width: 1fr;
    }
    .sidebar-q-kicker {
        height: 1;
        color: #666;
        text-style: bold;
    }
    _SidebarQueryRow.-active .sidebar-q-kicker {
        color: #86efac;
    }
    .sidebar-q-title {
        height: 1;
        color: #e8e8e8;
        text-style: bold;
    }
    .sidebar-section {
        height: 1;
        width: 100%;
        color: #555;
        padding: 0 1 0 4;
        margin-top: 1;
    }
    _SidebarResourceRow {
        height: 2;
        min-height: 2;
        max-height: 2;
        width: 100%;
        padding: 0 1;
        border-left: solid #0c0c0c;
    }
    _SidebarResourceRow:hover {
        background: #161616;
        border-left: solid #666;
    }
    _SidebarResourceRow.-jump {
        background: #1a1a12;
        border-left: solid #fbbf24;
    }
    .sidebar-res-title {
        width: 1fr;
        height: 1;
        color: #d8d8d8;
        content-align: left middle;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._jump_kind: Optional[JumpKind] = None
        self._jump_targets: list[Any] = []
        self._active_query_id: Optional[int] = None

    def compose(self) -> ComposeResult:
        yield Label(
            "[bold #86efac]1[/] Queries",
            id="sidebar-queries-kicker",
            markup=True,
        )
        yield Label("+ Reset draft", id="sidebar-new-query")
        yield VerticalScroll(id="sidebar-query-list")
        yield Label(
            "[bold #86efac]2[/] Workspace",
            id="sidebar-ws-kicker",
            markup=True,
        )
        yield VerticalScroll(id="sidebar-ws-list")

    def watch_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set_class(collapsed, "-collapsed")

    def activate_query(self, query_id: int) -> None:
        self.cancel_jump()
        self.post_message(self.QuerySelected(query_id))

    def activate_resource(self, resource_type: str, resource_id: str) -> None:
        self.cancel_jump()
        self.post_message(self.ResourceSelected(resource_type, resource_id))

    def handle_digit(self, digit: int) -> bool:
        """Handle ``1``–``9`` (and ``0`` as 10). Return True if consumed."""
        if digit < 0 or digit > 9:
            return False
        if self._jump_kind is None:
            if digit == 1:
                self.start_jump("queries")
                return True
            if digit == 2:
                self.start_jump("workspace")
                return True
            return False
        index = 10 if digit == 0 else digit
        return self.pick_jump(index)

    def start_jump(self, kind: JumpKind) -> None:
        if self.sidebar_collapsed:
            self.sidebar_collapsed = False
        self._jump_kind = kind
        self.set_class(kind == "queries", "-jump-queries")
        self.set_class(kind == "workspace", "-jump-workspace")
        self._apply_jump_numbers()
        n = len(self._jump_targets)
        if n == 0:
            label = "queries" if kind == "queries" else "workspace items"
            try:
                self.app.notify(f"No {label} to jump to", severity="warning")
            except Exception:  # noqa: BLE001
                pass
            self.cancel_jump()
            return
        try:
            self.app.notify(
                f"Jump: press 1–{min(n, 9)} (Esc cancels)",
                timeout=3,
            )
        except Exception:  # noqa: BLE001
            pass

    def cancel_jump(self) -> bool:
        """Clear jump mode. Return True if a mode was active."""
        if self._jump_kind is None:
            return False
        self._jump_kind = None
        self.remove_class("-jump-queries")
        self.remove_class("-jump-workspace")
        for row in self.query("_SidebarQueryRow"):
            if isinstance(row, _SidebarQueryRow):
                row.set_jump(None)
        for row in self.query("_SidebarResourceRow"):
            if isinstance(row, _SidebarResourceRow):
                row.set_jump(None)
        self._jump_targets = []
        return True

    def pick_jump(self, index: int) -> bool:
        if self._jump_kind is None or index < 1:
            return False
        if index > len(self._jump_targets):
            try:
                self.app.notify(f"No item {index}", severity="warning")
            except Exception:  # noqa: BLE001
                pass
            return True
        target = self._jump_targets[index - 1]
        kind = self._jump_kind
        self.cancel_jump()
        if kind == "queries" and isinstance(target, _SidebarQueryRow):
            self.post_message(self.QuerySelected(target.query_id))
        elif kind == "workspace" and isinstance(target, _SidebarResourceRow):
            self.post_message(
                self.ResourceSelected(target.resource_type, target.resource_id)
            )
        return True

    def _apply_jump_numbers(self) -> None:
        self._jump_targets = []
        for row in self.query("_SidebarQueryRow"):
            if isinstance(row, _SidebarQueryRow):
                row.set_jump(None)
        for row in self.query("_SidebarResourceRow"):
            if isinstance(row, _SidebarResourceRow):
                row.set_jump(None)
        if self._jump_kind == "queries":
            rows = [
                r
                for r in self.query_one(
                    "#sidebar-query-list", VerticalScroll
                ).children
                if isinstance(r, _SidebarQueryRow)
            ]
        elif self._jump_kind == "workspace":
            rows = [
                r
                for r in self.query_one(
                    "#sidebar-ws-list", VerticalScroll
                ).children
                if isinstance(r, _SidebarResourceRow)
            ]
        else:
            return
        for i, row in enumerate(rows[:9], start=1):
            row.set_jump(i)
            self._jump_targets.append(row)

    def refresh_queries(
        self,
        queries: list[dict[str, Any]],
        *,
        active_query_id: Optional[int] = None,
    ) -> None:
        """``queries`` items: ``{id, name, in_report, empty?}``."""
        self._active_query_id = active_query_id
        listing = self.query_one("#sidebar-query-list", VerticalScroll)
        self.run_worker(
            self._refresh_queries_async(listing, queries, active_query_id),
            exclusive=True,
            group="sidebar-queries",
            name="sidebar-queries",
        )

    async def _refresh_queries_async(
        self,
        listing: VerticalScroll,
        queries: list[dict[str, Any]],
        active_query_id: Optional[int],
    ) -> None:
        await listing.remove_children()
        drafts = [q for q in queries if not q.get("in_report")]
        saved = [q for q in queries if q.get("in_report")]
        drafts.sort(key=lambda q: int(q["id"]))
        draft = drafts[-1] if drafts else None
        widgets: list[Static] = []
        if draft is not None:
            label = str(draft.get("name") or "")
            widgets.append(
                _SidebarQueryRow(
                    int(draft["id"]),
                    label,
                    draft=True,
                    empty=bool(draft.get("empty")) or not label,
                    active=active_query_id == draft["id"],
                )
            )
        for q in saved:
            label = str(q.get("name") or f"Query {q['id']}")
            widgets.append(
                _SidebarQueryRow(
                    int(q["id"]),
                    label,
                    draft=False,
                    active=active_query_id == q["id"],
                )
            )
        if widgets:
            await listing.mount(*widgets)
        if self._jump_kind == "queries":
            self._apply_jump_numbers()

    def refresh_workspace(
        self,
        *,
        entities: list[tuple[str, str]],
        metrics: list[tuple[str, str]],
        series: list[tuple[str, str]],
    ) -> None:
        listing = self.query_one("#sidebar-ws-list", VerticalScroll)
        self.run_worker(
            self._refresh_workspace_async(listing, entities, metrics, series),
            exclusive=True,
            group="sidebar-ws",
            name="sidebar-ws",
        )
        total = len(entities) + len(metrics) + len(series)
        self.query_one("#sidebar-ws-kicker", Label).update(
            f"[bold #86efac]2[/] Workspace  {total}"
        )
        self.set_class(total == 0, "-ws-empty")

    async def _refresh_workspace_async(
        self,
        listing: VerticalScroll,
        entities: list[tuple[str, str]],
        metrics: list[tuple[str, str]],
        series: list[tuple[str, str]],
    ) -> None:
        await listing.remove_children()
        widgets: list[Static] = []

        def _section(title: str, rows: list[tuple[str, str]], kind: str) -> None:
            if not rows:
                return
            widgets.append(_SidebarSectionHeader(title))
            for rid, label in rows:
                widgets.append(
                    _SidebarResourceRow(kind, rid, label or rid)
                )

        _section("Entities", entities, "entity")
        _section("Metrics", metrics, "metric")
        _section("Series", series, "series")
        if widgets:
            await listing.mount(*widgets)
        if self._jump_kind == "workspace":
            self._apply_jump_numbers()

    def on_click(self, event) -> None:  # noqa: ANN001
        if event.widget and event.widget.id == "sidebar-new-query":
            self.cancel_jump()
            self.post_message(self.NewQuery())
