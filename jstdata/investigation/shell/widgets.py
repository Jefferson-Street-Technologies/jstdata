"""Shell chrome widgets for the chart-first research layout."""

from __future__ import annotations

from typing import Any, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView, Static, Tree
from textual.widgets.tree import TreeNode


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
        hint = "   [: commands  ? help]" if commands_hint else ""
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


class _SidebarQueryItem(ListItem):
    def __init__(self, query_id: int, label: str, *, draft: bool) -> None:
        self.query_id = query_id
        self.draft = draft
        prefix = "·" if draft else ""
        display = f"{prefix}{query_id:02d} {label}"
        super().__init__(Label(display))


class _SidebarHeaderItem(ListItem):
    """Section label inside the query list — not selectable."""

    def __init__(self, text: str) -> None:
        super().__init__(Label(text, markup=True))
        self.disabled = True


class ResearchSidebar(Static):
    """Left sidebar: queries (draft/saved) + workspace resources."""

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

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "activate", "Open", show=False, priority=True),
    ]

    sidebar_collapsed: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    ResearchSidebar {
        width: 28;
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
    }
    #sidebar-query-list {
        height: 1fr;
        max-height: 55%;
        background: #0c0c0c;
        padding: 0 0 0 0;
    }
    #sidebar-ws-tree {
        height: 1fr;
        max-height: 45%;
        background: #0c0c0c;
        padding: 0 1;
    }
    ResearchSidebar.-ws-empty #sidebar-query-list {
        max-height: 75%;
    }
    ResearchSidebar.-ws-empty #sidebar-ws-tree {
        max-height: 6;
        height: 6;
    }
    #sidebar-query-list > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    #sidebar-query-list > ListItem:disabled {
        color: #666;
        background: transparent;
        border-left: solid #0c0c0c;
    }
    #sidebar-query-list > ListItem:disabled.--highlight {
        background: transparent;
        border-left: solid #0c0c0c;
        color: #666;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(
            "[bold #86efac]1[/] Queries",
            id="sidebar-queries-kicker",
            markup=True,
        )
        yield Label("+ New query", id="sidebar-new-query")
        yield ListView(id="sidebar-query-list")
        yield Label(
            "[bold #86efac]2[/] Workspace",
            id="sidebar-ws-kicker",
            markup=True,
        )
        yield Tree("Resources", id="sidebar-ws-tree")

    def _nav_target(self) -> ListView | Tree | None:
        focused = self.screen.focused if self.screen else None
        if focused is None:
            return None
        listing = self.query_one("#sidebar-query-list", ListView)
        tree = self.query_one("#sidebar-ws-tree", Tree)
        node = focused
        while node is not None:
            if node is listing or node is tree:
                return node  # type: ignore[return-value]
            node = node.parent
        return None

    def _query_items(self) -> list[ListItem]:
        listing = self.query_one("#sidebar-query-list", ListView)
        return [c for c in listing.children if isinstance(c, ListItem)]

    def _move_query_cursor(self, delta: int) -> None:
        """Move highlight only onto real queries — skip Drafts/Saved headers."""
        listing = self.query_one("#sidebar-query-list", ListView)
        items = self._query_items()
        if not items:
            return
        idx = listing.index if listing.index is not None else 0
        n = len(items)
        for _ in range(n):
            idx += delta
            if idx < 0 or idx >= n:
                return
            if isinstance(items[idx], _SidebarQueryItem):
                listing.index = idx
                return

    def action_cursor_down(self) -> None:
        target = self._nav_target()
        if isinstance(target, ListView) and target.id == "sidebar-query-list":
            self._move_query_cursor(1)
            return
        if target is not None and hasattr(target, "action_cursor_down"):
            target.action_cursor_down()

    def action_cursor_up(self) -> None:
        target = self._nav_target()
        if isinstance(target, ListView) and target.id == "sidebar-query-list":
            self._move_query_cursor(-1)
            return
        if target is not None and hasattr(target, "action_cursor_up"):
            target.action_cursor_up()

    def action_activate(self) -> None:
        """Enter in the sidebar opens the highlighted query / workspace node."""
        target = self._nav_target()
        if isinstance(target, ListView) and target.id == "sidebar-query-list":
            item = target.highlighted_child
            if isinstance(item, _SidebarQueryItem):
                self.post_message(self.QuerySelected(item.query_id))
            return
        if isinstance(target, Tree):
            target.action_select_cursor()

    def watch_sidebar_collapsed(self, collapsed: bool) -> None:
        self.set_class(collapsed, "-collapsed")

    def refresh_queries(
        self,
        queries: list[dict[str, Any]],
        *,
        active_query_id: Optional[int] = None,
    ) -> None:
        """``queries`` items: ``{id, name, in_report}``."""
        listing = self.query_one("#sidebar-query-list", ListView)
        listing.clear()
        drafts = [q for q in queries if not q.get("in_report")]
        saved = [q for q in queries if q.get("in_report")]
        highlight_index = 0
        i = 0
        if drafts:
            listing.append(_SidebarHeaderItem("[dim]— Drafts —[/]"))
            i += 1
            for q in drafts:
                label = q.get("name") or f"Query {q['id']}"
                listing.append(
                    _SidebarQueryItem(int(q["id"]), str(label), draft=True)
                )
                if active_query_id == q["id"]:
                    highlight_index = i
                i += 1
        if saved:
            listing.append(_SidebarHeaderItem("[dim]— Saved —[/]"))
            i += 1
            for q in saved:
                label = q.get("name") or f"Query {q['id']}"
                listing.append(
                    _SidebarQueryItem(int(q["id"]), str(label), draft=False)
                )
                if active_query_id == q["id"]:
                    highlight_index = i
                i += 1
        if not drafts and not saved:
            listing.append(_SidebarHeaderItem("[dim](no queries)[/]"))
        # Never leave the cursor parked on a section header.
        items = [c for c in listing.children if isinstance(c, ListItem)]
        if items and not isinstance(items[highlight_index], _SidebarQueryItem):
            for j, item in enumerate(items):
                if isinstance(item, _SidebarQueryItem):
                    highlight_index = j
                    break
        listing.index = highlight_index

    def refresh_workspace(
        self,
        *,
        entities: list[tuple[str, str]],
        metrics: list[tuple[str, str]],
        series: list[tuple[str, str]],
    ) -> None:
        tree = self.query_one("#sidebar-ws-tree", Tree)
        tree.clear()
        root = tree.root
        root.expand()

        def _branch(title: str, rows: list[tuple[str, str]], kind: str) -> None:
            node = root.add(f"{title}  {len(rows)}", expand=True)
            for rid, label in rows:
                child = node.add_leaf(label or rid)
                child.data = {"type": kind, "id": rid}

        _branch("Entities", entities, "entity")
        _branch("Metrics", metrics, "metric")
        _branch("Series", series, "series")
        ws_kicker = self.query_one("#sidebar-ws-kicker", Label)
        total = len(entities) + len(metrics) + len(series)
        ws_kicker.update(f"[bold #86efac]2[/] Workspace  {total}")
        self.set_class(total == 0, "-ws-empty")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _SidebarQueryItem):
            self.post_message(self.QuerySelected(item.query_id))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Arrow keys / mouse can still land on section headers — bounce off.
        if event.list_view is None or event.list_view.id != "sidebar-query-list":
            return
        if not isinstance(event.item, _SidebarHeaderItem):
            return
        listing = event.list_view
        items = self._query_items()
        idx = listing.index if listing.index is not None else 0
        for j in range(idx + 1, len(items)):
            if isinstance(items[j], _SidebarQueryItem):
                listing.index = j
                return
        for j in range(idx - 1, -1, -1):
            if isinstance(items[j], _SidebarQueryItem):
                listing.index = j
                return

    def on_tree_node_selected(self, event: Tree.NodeSelected[Any]) -> None:
        node: TreeNode[Any] = event.node
        data = getattr(node, "data", None)
        if isinstance(data, dict) and data.get("id"):
            self.post_message(
                self.ResourceSelected(str(data["type"]), str(data["id"]))
            )

    def on_click(self, event) -> None:  # noqa: ANN001
        if event.widget and event.widget.id == "sidebar-new-query":
            self.post_message(self.NewQuery())
