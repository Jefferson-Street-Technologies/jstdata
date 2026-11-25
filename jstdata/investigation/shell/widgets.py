"""Shell chrome widgets for the chart-first research layout."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Label, Static

JumpKind = Literal["queries", "workspace"]
# ("query", query_id) or ("resource", resource_type, resource_id)
ArmedRemove = Union[tuple[str, int], tuple[str, str, str]]


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


def _sidebar_owner(node: Any) -> Optional["ResearchSidebar"]:
    parent = getattr(node, "parent", None)
    while parent is not None and not isinstance(parent, ResearchSidebar):
        parent = getattr(parent, "parent", None)
    return parent if isinstance(parent, ResearchSidebar) else None


def _clicked_remove(event: Any) -> bool:
    widget = getattr(event, "widget", None)
    while widget is not None:
        classes = getattr(widget, "classes", None) or ()
        if "sidebar-remove" in classes:
            return True
        widget = getattr(widget, "parent", None)
    return False


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
            yield Label("✕", classes="sidebar-remove")

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

    def on_click(self, event) -> None:  # noqa: ANN001
        sidebar = _sidebar_owner(self)
        if sidebar is None:
            return
        if _clicked_remove(event):
            event.stop()
            sidebar.remove_query(self.query_id)
            return
        sidebar.activate_query(self.query_id)


class _SidebarSectionHeader(Static):
    """Subheader between flat workspace resource groups."""

    def __init__(self, title: str, *, emphasis: bool = False) -> None:
        classes = "sidebar-section sidebar-section-strong" if emphasis else "sidebar-section"
        text = f"[bold]{title}[/]" if emphasis else f"[dim]{title}[/]"
        super().__init__(text, markup=True, classes=classes)


class _SidebarResourceRow(Static):
    """Clickable flat workspace resource (entity / metric / series)."""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        label: str,
        *,
        removable: bool = True,
    ) -> None:
        super().__init__()
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.removable = removable
        self._label = label or resource_id
        self.jump_index: Optional[int] = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="sidebar-row"):
            yield Label(" ", classes="sidebar-jump-num", markup=True)
            yield Label(self._label, classes="sidebar-res-title")
            if self.removable:
                yield Label("✕", classes="sidebar-remove")

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

    def on_click(self, event) -> None:  # noqa: ANN001
        sidebar = _sidebar_owner(self)
        if sidebar is None:
            return
        if _clicked_remove(event):
            event.stop()
            sidebar.remove_resource(self.resource_type, self.resource_id)
            return
        sidebar.activate_resource(self.resource_type, self.resource_id)


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

    class QueryRemoved(Message):
        def __init__(self, query_id: int) -> None:
            self.query_id = query_id
            super().__init__()

    class ResourceRemoved(Message):
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
    .sidebar-section-strong {
        color: #86efac;
        padding: 0 1 0 1;
        margin-top: 0;
        text-style: bold;
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
    .sidebar-remove {
        width: 3;
        height: 1;
        color: #f87171;
        text-style: bold;
        content-align: center middle;
        dock: right;
    }
    .sidebar-remove:hover {
        color: #fecaca;
        background: #3f1d1d;
    }
    .sidebar-remove.-armed {
        width: auto;
        min-width: 7;
        color: #fecaca;
        background: #7f1d1d;
        text-style: bold;
        padding: 0 1;
    }
    _SidebarQueryRow .sidebar-remove {
        margin-top: 0;
    }
    _SidebarQueryRow.-armed-remove, _SidebarResourceRow.-armed-remove {
        background: #2a1212;
        border-left: solid #f87171;
    }
    """

    _ARM_SECONDS = 2.5

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._jump_kind: Optional[JumpKind] = None
        self._jump_targets: list[Any] = []
        self._active_query_id: Optional[int] = None
        self._pending_queries: Optional[list[dict[str, Any]]] = None
        self._pending_workspace: Optional[
            tuple[
                list[tuple[str, str]],
                list[tuple[str, str]],
                list[tuple[str, str]],
            ]
        ] = None
        self._pending_active: Optional[
            tuple[
                list[tuple[str, str]],
                list[tuple[str, str]],
                list[tuple[str, str]],
            ]
        ] = None
        self._queries_running = False
        self._workspace_running = False
        self._queries_token = 0
        self._workspace_token = 0
        self._armed_remove: Optional[ArmedRemove] = None
        self._armed_timer: Optional[Timer] = None

    def compose(self) -> ComposeResult:
        yield Label("Queries", id="sidebar-queries-kicker")
        yield Label("+ Reset draft", id="sidebar-new-query")
        yield VerticalScroll(id="sidebar-query-list")
        yield Label("Workspace", id="sidebar-ws-kicker")
        yield VerticalScroll(id="sidebar-ws-list")

    def watch_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set_class(collapsed, "-collapsed")

    def activate_query(self, query_id: int) -> None:
        self.cancel_jump()
        self.clear_armed_remove()
        self.post_message(self.QuerySelected(query_id))

    def activate_resource(self, resource_type: str, resource_id: str) -> None:
        self.cancel_jump()
        self.clear_armed_remove()
        self.post_message(self.ResourceSelected(resource_type, resource_id))

    def remove_query(self, query_id: int) -> None:
        """First ✕ arms the tile; second confirms delete (no modal)."""
        self.cancel_jump()
        key: ArmedRemove = ("query", int(query_id))
        if self._armed_remove == key:
            self.clear_armed_remove()
            self.post_message(self.QueryRemoved(int(query_id)))
            return
        self._arm_remove(key)

    def remove_resource(self, resource_type: str, resource_id: str) -> None:
        """First ✕ arms the tile; second confirms remove (no modal)."""
        self.cancel_jump()
        key: ArmedRemove = ("resource", resource_type, resource_id)
        if self._armed_remove == key:
            self.clear_armed_remove()
            self.post_message(self.ResourceRemoved(resource_type, resource_id))
            return
        self._arm_remove(key)

    def clear_armed_remove(self) -> None:
        if self._armed_timer is not None:
            self._armed_timer.stop()
            self._armed_timer = None
        prev = self._armed_remove
        self._armed_remove = None
        if prev is not None:
            self._paint_armed_remove(prev, armed=False)

    def _arm_remove(self, key: ArmedRemove) -> None:
        if self._armed_remove is not None and self._armed_remove != key:
            self._paint_armed_remove(self._armed_remove, armed=False)
        self._armed_remove = key
        self._paint_armed_remove(key, armed=True)
        if self._armed_timer is not None:
            self._armed_timer.stop()
        self._armed_timer = self.set_timer(
            self._ARM_SECONDS, self.clear_armed_remove
        )

    def _paint_armed_remove(self, key: ArmedRemove, *, armed: bool) -> None:
        row: Optional[Static] = None
        if key[0] == "query":
            qid = int(key[1])
            for candidate in self.query(_SidebarQueryRow):
                if isinstance(candidate, _SidebarQueryRow) and candidate.query_id == qid:
                    row = candidate
                    break
        else:
            rtype, rid = key[1], key[2]
            # Prefer a removable (Saved) row when Active + Saved share an id.
            fallback: Optional[Static] = None
            for candidate in self.query(_SidebarResourceRow):
                if not (
                    isinstance(candidate, _SidebarResourceRow)
                    and candidate.resource_type == rtype
                    and candidate.resource_id == rid
                ):
                    continue
                if candidate.removable:
                    row = candidate
                    break
                if fallback is None:
                    fallback = candidate
            if row is None:
                row = fallback
        if row is None:
            return
        row.set_class(armed, "-armed-remove")
        try:
            btn = row.query_one(".sidebar-remove", Label)
        except Exception:  # noqa: BLE001
            return
        btn.set_class(armed, "-armed")
        btn.update("again" if armed else "✕")

    def handle_digit(self, digit: int) -> bool:
        """Pick a numbered jump target. Return True if consumed.

        Enter jump mode with ``Q`` / ``w`` first — digits alone do nothing.
        """
        if digit < 0 or digit > 9:
            return False
        if self._jump_kind is None:
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
        self._pending_queries = list(queries)
        self._queries_token += 1
        # Defer worker start until the app event loop is ready.
        self.call_later(self._launch_refresh_queries)

    def _launch_refresh_queries(self) -> None:
        if self.app is None or not self.is_mounted:
            return
        if getattr(self, "_pending_queries", None) is None:
            return
        if self._queries_running:
            return
        # Guard before scheduling so overlapping call_later can't triple-mount.
        self._queries_running = True
        # Never exclusive-cancel DOM work — cancel mid-remove can blank-quit.
        self.run_worker(
            self._refresh_queries_async,
            exclusive=False,
            group="sidebar-queries",
            name="sidebar-queries",
            exit_on_error=False,
        )

    async def _refresh_queries_async(self) -> None:
        try:
            while self.is_mounted:
                token = self._queries_token
                queries = getattr(self, "_pending_queries", None)
                if queries is None:
                    return
                # Dom rebuild drops query ✕ widgets — drop an armed query only.
                if self._armed_remove and self._armed_remove[0] == "query":
                    self.clear_armed_remove()
                active_query_id = self._active_query_id
                listing = self.query_one("#sidebar-query-list", VerticalScroll)
                await listing.remove_children()
                if not self.is_mounted:
                    return
                if token != self._queries_token:
                    continue
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
                if token == self._queries_token:
                    return
        finally:
            self._queries_running = False

    def refresh_workspace(
        self,
        *,
        entities: list[tuple[str, str]],
        metrics: list[tuple[str, str]],
        series: list[tuple[str, str]],
        active_entities: Optional[list[tuple[str, str]]] = None,
        active_metrics: Optional[list[tuple[str, str]]] = None,
        active_series: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        self._pending_workspace = (entities, metrics, series)
        ae = list(active_entities or [])
        am = list(active_metrics or [])
        a_s = list(active_series or [])
        self._pending_active = (ae, am, a_s) if (ae or am or a_s) else None
        self._workspace_token += 1
        total = len(entities) + len(metrics) + len(series)
        active_n = len(ae) + len(am) + len(a_s)
        if total and active_n:
            kicker = f"Workspace  {total} · active {active_n}"
        elif total:
            kicker = f"Workspace  {total}"
        elif active_n:
            kicker = f"Workspace · active {active_n}"
        else:
            kicker = "Workspace"
        self.query_one("#sidebar-ws-kicker", Label).update(kicker)
        self.set_class(total == 0 and active_n == 0, "-ws-empty")
        self.call_later(self._launch_refresh_workspace)

    def _launch_refresh_workspace(self) -> None:
        if self.app is None or not self.is_mounted:
            return
        if getattr(self, "_pending_workspace", None) is None:
            return
        if self._workspace_running:
            return
        # Guard before scheduling so overlapping call_later can't triple-mount.
        self._workspace_running = True
        self.run_worker(
            self._refresh_workspace_async,
            exclusive=False,
            group="sidebar-ws",
            name="sidebar-ws",
            exit_on_error=False,
        )

    async def _refresh_workspace_async(self) -> None:
        try:
            while self.is_mounted:
                token = self._workspace_token
                pending = getattr(self, "_pending_workspace", None)
                if pending is None:
                    return
                if self._armed_remove and self._armed_remove[0] == "resource":
                    self.clear_armed_remove()
                entities, metrics, series = pending
                active = getattr(self, "_pending_active", None)
                listing = self.query_one("#sidebar-ws-list", VerticalScroll)
                await listing.remove_children()
                if not self.is_mounted:
                    return
                if token != self._workspace_token:
                    continue
                widgets: list[Static] = []

                def _section(
                    title: str,
                    rows: list[tuple[str, str]],
                    kind: str,
                    *,
                    removable: bool = True,
                ) -> None:
                    if not rows:
                        return
                    widgets.append(_SidebarSectionHeader(title))
                    for rid, label in rows:
                        widgets.append(
                            _SidebarResourceRow(
                                kind,
                                rid,
                                label or rid,
                                removable=removable,
                            )
                        )

                if active is not None:
                    ae, am, a_s = active
                    if ae or am or a_s:
                        widgets.append(
                            _SidebarSectionHeader("Active", emphasis=True)
                        )
                        _section("Entities", ae, "entity", removable=False)
                        _section("Metrics", am, "metric", removable=False)
                        _section("Series", a_s, "series", removable=False)
                        if entities or metrics or series:
                            widgets.append(
                                _SidebarSectionHeader("Saved", emphasis=True)
                            )

                _section("Entities", entities, "entity")
                _section("Metrics", metrics, "metric")
                _section("Series", series, "series")
                if widgets:
                    await listing.mount(*widgets)
                if self._jump_kind == "workspace":
                    self._apply_jump_numbers()
                if token == self._workspace_token:
                    return
        finally:
            self._workspace_running = False

    def on_click(self, event) -> None:  # noqa: ANN001
        if event.widget and event.widget.id == "sidebar-new-query":
            self.cancel_jump()
            self.post_message(self.NewQuery())
