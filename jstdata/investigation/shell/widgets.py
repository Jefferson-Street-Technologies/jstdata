"""Shell chrome widgets for the chart-first research layout."""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import Label, Static

from ..ui_primitives import ActionButton

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

    def set_project(self, title: str, *, commands_hint: bool = False) -> None:
        # Commands live on the pane bar chip; optional hint for non-shell UIs.
        hint = "   [: commands]" if commands_hint else ""
        self.update(f"JST / {title}{hint}")


class _InvTabChip(Static):
    """One investigation tab chip (neovim-style)."""

    DEFAULT_CSS = """
    _InvTabChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #a3a3a3;
        background: #161616;
    }
    _InvTabChip.-active {
        color: #0a0a0a;
        background: #4ade80;
        text-style: bold;
    }
    _InvTabChip.-jump {
        text-style: bold;
    }
    """

    def __init__(
        self,
        investigation_id: str,
        label: str,
        *,
        active: bool = False,
        mark: str = "",
    ) -> None:
        super().__init__(markup=True)
        self.investigation_id = investigation_id
        self._label = label
        self._mark = mark
        self.active = active
        self.jump_index: Optional[int] = None

    def on_mount(self) -> None:
        self.set_class(self.active, "-active")
        self._paint()

    def set_jump(self, index: Optional[int]) -> None:
        self.jump_index = index
        self.set_class(index is not None, "-jump")
        self._paint()

    def _paint(self) -> None:
        mark = self._mark or ""
        name = f"{self._label}{mark}"
        if self.jump_index is not None:
            text = f"[bold #fbbf24]{self.jump_index}[/] {name}"
        else:
            text = name
        self.update(text)

    def on_click(self) -> None:
        strip = self.parent
        while strip is not None and not isinstance(strip, TabBar):
            strip = strip.parent
        if isinstance(strip, TabBar):
            strip.post_message(TabBar.TabSelected(self.investigation_id))


class TabBar(Horizontal):
    """Investigation tabs for the pane chrome (hidden when only one tab)."""

    class TabSelected(Message):
        def __init__(self, investigation_id: str) -> None:
            self.investigation_id = investigation_id
            super().__init__()

    DEFAULT_CSS = """
    TabBar {
        height: 1;
        width: 1fr;
        layout: horizontal;
        align: right middle;
        background: #0c0c0c;
        padding: 0 0 0 1;
        overflow-x: auto;
    }
    TabBar.-empty {
        display: none;
    }
    TabBar.-jump _InvTabChip {
        color: #e5e5e5;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._order: list[str] = []
        self._labels: dict[str, str] = {}
        self._active: Optional[str] = None
        self._marks: dict[str, str] = {}
        self._jumping = False
        self._jump_targets: list[_InvTabChip] = []
        self._rebuild_token = 0
        self._rebuild_running = False

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
        # Single tab → no strip (neovim-style multi-tab only).
        show = len(self._order) > 1
        self.set_class(not show, "-empty")
        if self.is_mounted:
            self._schedule_rebuild()

    def on_mount(self) -> None:
        self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        self._rebuild_token += 1
        self.call_later(self._launch_rebuild)

    def _launch_rebuild(self) -> None:
        if self.app is None or not self.is_mounted:
            return
        if self._rebuild_running:
            return
        self._rebuild_running = True
        self.run_worker(
            self._rebuild_tabs,
            exclusive=False,
            group=f"tab-bar-{id(self)}",
            name="tab-bar-rebuild",
            exit_on_error=False,
        )

    async def _rebuild_tabs(self) -> None:
        try:
            while self.is_mounted:
                token = self._rebuild_token
                was_jumping = self._jumping
                self.cancel_jump()
                await self.remove_children()
                if not self.is_mounted:
                    return
                if token != self._rebuild_token:
                    continue
                if len(self._order) <= 1:
                    self.set_class(True, "-empty")
                    return
                self.set_class(False, "-empty")
                for inv_id in self._order:
                    if not self.is_mounted or token != self._rebuild_token:
                        break
                    label = self._labels.get(inv_id, inv_id)
                    await self.mount(
                        _InvTabChip(
                            inv_id,
                            label,
                            active=inv_id == self._active,
                            mark=self._marks.get(inv_id, ""),
                        )
                    )
                else:
                    if was_jumping and self.is_mounted:
                        self.start_jump()
                    return
                # Token changed mid-mount — rebuild again.
        finally:
            self._rebuild_running = False

    def start_jump(self) -> bool:
        """Number tabs for Shift+T jump. Return False if nothing to jump to."""
        if len(self._order) <= 1:
            return False
        self._jumping = True
        self.add_class("-jump")
        self._jump_targets = []
        chips = [c for c in self.children if isinstance(c, _InvTabChip)]
        for i, chip in enumerate(chips[:9], start=1):
            chip.set_jump(i)
            self._jump_targets.append(chip)
        for chip in chips[9:]:
            chip.set_jump(None)
        return bool(self._jump_targets)

    def cancel_jump(self) -> bool:
        if not self._jumping:
            return False
        self._jumping = False
        self.remove_class("-jump")
        for chip in self.query(_InvTabChip):
            if isinstance(chip, _InvTabChip):
                chip.set_jump(None)
        self._jump_targets = []
        return True

    def handle_digit(self, digit: int) -> bool:
        if not self._jumping:
            return False
        if digit < 0 or digit > 9:
            return False
        index = 10 if digit == 0 else digit
        if index < 1 or index > len(self._jump_targets):
            try:
                self.app.notify(f"No tab {index}", severity="warning")
            except Exception:  # noqa: BLE001
                pass
            return True
        chip = self._jump_targets[index - 1]
        self.cancel_jump()
        self.post_message(self.TabSelected(chip.investigation_id))
        return True

    def render(self):  # noqa: ANN201
        # Unit-test helper when unmounted: mimic the old Static text.
        if self.is_mounted:
            return super().render()
        parts: list[str] = []
        for i, inv_id in enumerate(self._order, start=1):
            label = self._labels.get(inv_id, inv_id)
            mark = self._marks.get(inv_id, "")
            text = f"{i} {label}{mark}"
            if inv_id == self._active:
                parts.append(f"[{text}]")
            else:
                parts.append(f"[{text}]")
        return "  ".join(parts)


class PaneBar(Static):
    """Chrome strip: button chips left, always-on tabs + ``+`` right.

    Left: Home / Search / Commands. Tabs always paint (even one). Each tab
    gets a close ``×`` when there are 2+ tabs. Trailing ``+`` opens a new
    tab. Shift+T enters a tab menu (numbers / N / X).

    Painted as a Static — docked/top Horizontals clip to height 0.
    """

    class Home(Message):
        pass

    class Search(Message):
        pass

    class Commands(Message):
        pass

    class NewTab(Message):
        pass

    class TabSelected(Message):
        def __init__(self, investigation_id: str) -> None:
            self.investigation_id = investigation_id
            super().__init__()

    class TabClose(Message):
        def __init__(self, investigation_id: str) -> None:
            self.investigation_id = investigation_id
            super().__init__()

    _HIT_HOME = "home"
    _HIT_SEARCH = "search"
    _HIT_COMMANDS = "commands"
    _HIT_TAB = "tab"
    _HIT_CLOSE = "close"
    _HIT_NEW = "new_tab"

    DEFAULT_CSS = """
    PaneBar {
        height: 1;
        width: 100%;
        background: #0c0c0c;
        color: #e5e5e5;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", markup=True, **kwargs)
        self._order: list[str] = []
        self._labels: dict[str, str] = {}
        self._active: Optional[str] = None
        self._marks: dict[str, str] = {}
        self._jumping = False
        # (start, end, kind, investigation_id|None)
        self._hits: list[tuple[int, int, str, Optional[str]]] = []
        self._jump_targets: list[str] = []

    def on_mount(self) -> None:
        self._paint()

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
        if self._jumping:
            self._jump_targets = list(self._order[:9])
        self._paint()

    def start_jump(self) -> bool:
        """Open the tab menu. Always ok — even one tab (N adds, X blocked)."""
        self._jumping = True
        self._jump_targets = list(self._order[:9])
        self._paint()
        return True

    def cancel_jump(self) -> bool:
        if not self._jumping:
            return False
        self._jumping = False
        self._jump_targets = []
        self._paint()
        return True

    def handle_digit(self, digit: int) -> bool:
        if not self._jumping:
            return False
        if digit < 0 or digit > 9:
            return False
        index = 10 if digit == 0 else digit
        if index < 1 or index > len(self._jump_targets):
            try:
                self.app.notify(f"No tab {index}", severity="warning")
            except Exception:  # noqa: BLE001
                pass
            return True
        inv_id = self._jump_targets[index - 1]
        self.cancel_jump()
        self.post_message(self.TabSelected(inv_id))
        return True

    @staticmethod
    def _chip(plain: str, *, key: str) -> tuple[str, str]:
        """Return (plain_with_padding, markup) for a left action chip."""
        body = f" {plain} "
        # Key hint stays dim inside the chip.
        if plain.endswith(f" {key}"):
            label = plain[: -len(f" {key}")]
            shown = (
                f"[on #2a2a2a] {label} [dim]{key}[/] [/]"
            )
        else:
            shown = f"[on #2a2a2a]{body}[/]"
        return body, shown

    def _paint(self) -> None:
        hits: list[tuple[int, int, str, Optional[str]]] = []
        parts: list[str] = []
        col = 0

        def emit(plain: str, shown: str, kind: str, inv: Optional[str] = None) -> None:
            nonlocal col
            start = col
            col += len(plain)
            hits.append((start, col, kind, inv))
            parts.append(shown)

        def gap(n: int = 1) -> None:
            nonlocal col
            col += n
            parts.append(" " * n)

        # Left action chips — look like buttons, not header text.
        for plain_label, key, kind in (
            ("Home", "H", self._HIT_HOME),
            ("Search", "/", self._HIT_SEARCH),
            ("Commands", ":", self._HIT_COMMANDS),
        ):
            if parts:
                gap(1)
            body, shown = self._chip(f"{plain_label} {key}", key=key)
            emit(body, shown, kind)

        gap(3)

        can_close = len(self._order) > 1
        for i, inv_id in enumerate(self._order, start=1):
            if i > 1:
                gap(1)
            label = self._labels.get(inv_id, inv_id)
            mark = self._marks.get(inv_id, "")
            active = inv_id == self._active
            if self._jumping and i <= 9:
                core_plain = f"{i} {label}{mark}"
                core_shown = f"[bold #fbbf24]{i}[/] {label}{mark}"
            else:
                core_plain = f"{label}{mark}"
                core_shown = label + mark

            if active:
                tab_plain = f" {core_plain} "
                tab_shown = f"[bold #0a0a0a on #4ade80]{tab_plain}[/]"
            else:
                tab_plain = f" {core_plain} "
                tab_shown = f"[on #1a1a1a]{tab_plain}[/]"

            emit(tab_plain, tab_shown, self._HIT_TAB, inv_id)

            if can_close:
                close_plain = "×"
                close_shown = "[dim #a3a3a3]×[/]" if not active else "[#0a0a0a on #4ade80]×[/]"
                # Keep close visually attached to the tab chip.
                if active:
                    # Active chip already closed; paint × as its own hit after.
                    close_shown = "[bold #0a0a0a on #4ade80] ×[/]"
                    close_plain = " ×"
                else:
                    close_shown = "[on #1a1a1a dim] ×[/]"
                    close_plain = " ×"
                emit(close_plain, close_shown, self._HIT_CLOSE, inv_id)

        # Trailing + always available for a new tab.
        gap(1)
        plus_plain = " + "
        plus_shown = (
            "[bold #fbbf24 on #2a2a2a] + [/]"
            if self._jumping
            else "[on #2a2a2a] + [/]"
        )
        emit(plus_plain, plus_shown, self._HIT_NEW)

        self._hits = hits
        self.update("".join(parts))

    def on_click(self, event) -> None:  # noqa: ANN001
        try:
            local_x = event.screen_x - self.region.x - 1  # padding 0 1
        except Exception:  # noqa: BLE001
            return
        if local_x < 0:
            return
        for start, end, action, inv in self._hits:
            if not (start <= local_x < end):
                continue
            if action == self._HIT_HOME:
                self.post_message(self.Home())
            elif action == self._HIT_SEARCH:
                self.post_message(self.Search())
            elif action == self._HIT_COMMANDS:
                self.post_message(self.Commands())
            elif action == self._HIT_NEW:
                if self._jumping:
                    self.cancel_jump()
                self.post_message(self.NewTab())
            elif action == self._HIT_TAB and inv:
                if self._jumping:
                    self.cancel_jump()
                self.post_message(self.TabSelected(inv))
            elif action == self._HIT_CLOSE and inv:
                if self._jumping:
                    self.cancel_jump()
                self.post_message(self.TabClose(inv))
            event.stop()
            return


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
        """Delete immediately — no arm / confirm step."""
        self.cancel_jump()
        self.clear_armed_remove()
        self.post_message(self.QueryRemoved(int(query_id)))

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
