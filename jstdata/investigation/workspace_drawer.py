"""Left workspace drawer — list / remove staged resources (no preview)."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView

from ..investigation.model import Workspace
from ..actions.base import label_for


class WorkspaceResourceRow(ListItem):
    """One staged resource in the workspace drawer list."""

    def __init__(self, resource_id: str, resource_type: str, display_name: str) -> None:
        super().__init__()
        self.resource_id = resource_id
        self.resource_type = resource_type
        self.display_name = display_name

    def compose(self) -> ComposeResult:
        yield Label(
            f"{self.resource_type.upper():<8} {self.display_name}  [{self.resource_id}]",
            classes="sess-row-label",
        )


class WorkspaceDrawer(ModalScreen[None]):
    """Non-dimming left drawer over the active investigation screen."""

    DEFAULT_CSS = """
    WorkspaceDrawer {
        align: left middle;
        background: transparent;
    }

    #ws-drawer-panel {
        width: 42;
        height: 100%;
        background: #111111;
        border-right: tall #4ade80;
        padding: 1 1;
    }

    #ws-drawer-title {
        text-style: bold;
        color: #4ade80;
        height: 1;
        margin-bottom: 1;
    }

    #ws-drawer-list {
        height: 1fr;
        background: #0a0a0a;
        border: solid #222;
    }

    WorkspaceResourceRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }

    WorkspaceResourceRow:hover,
    WorkspaceResourceRow.-highlighted {
        background: #1a3a1a;
    }

    .sess-row-label {
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

    #ws-drawer-hint {
        color: #888;
        height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("backspace", "remove", "Remove"),
        Binding("delete", "remove", "Remove", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("h", "prev_workspace", "Prev workspace", show=False),
        Binding("l", "next_workspace", "Next workspace", show=False),
        Binding("left", "prev_workspace", "Prev workspace", show=False),
        Binding("right", "next_workspace", "Next workspace", show=False),
    ]

    def __init__(self, workspace: Workspace, labels: dict[str, str]) -> None:
        super().__init__()
        self.workspace = workspace
        self.labels = labels

    def compose(self) -> ComposeResult:
        with Vertical(id="ws-drawer-panel"):
            yield Label("WORKSPACE", id="ws-drawer-title")
            yield ListView(id="ws-drawer-list")
            yield Label(
                "[bold]j/k[/bold] rows  ·  [bold]h/l[/bold] workspaces  ·  "
                "[bold]backspace[/bold] remove  ·  [bold]esc[/bold] close",
                id="ws-drawer-hint",
                markup=True,
            )

    def on_mount(self) -> None:
        self.rebuild()
        self._refresh_title()
        self.query_one("#ws-drawer-list", ListView).focus()

    def _refresh_title(self) -> None:
        wid = getattr(self.workspace, "id", "?")
        wlabel = getattr(self.workspace, "label", None) or f"workspace {wid}"
        self.query_one("#ws-drawer-title", Label).update(f"WORKSPACE · {wlabel}")

    def rebuild(self) -> None:
        list_view = self.query_one("#ws-drawer-list", ListView)
        list_view.clear()
        entries: list[tuple[str, str]] = []
        for mid in self.workspace.metric:
            entries.append(("metric", mid))
        for eid in self.workspace.entity:
            entries.append(("entity", eid))
        for sid in self.workspace.series:
            entries.append(("series", sid))
        if not entries:
            list_view.append(
                ListItem(Label("Workspace is empty", classes="sess-row-label"), disabled=True)
            )
            return
        for rtype, rid in entries:
            list_view.append(
                WorkspaceResourceRow(rid, rtype, label_for(self.labels, rid))
            )

    def _highlighted_row(self) -> WorkspaceResourceRow | None:
        child = self.query_one("#ws-drawer-list", ListView).highlighted_child
        return child if isinstance(child, WorkspaceResourceRow) else None

    def action_remove(self) -> None:
        row = self._highlighted_row()
        if row is None:
            return
        rid = row.resource_id
        self.workspace.remove_id(rid)
        self.labels.pop(rid, None)
        self.rebuild()
        self.notify(f"Removed {rid}")
        self.query_one("#ws-drawer-list", ListView).focus()
        # Ask host to persist workspace
        persist = getattr(self.app, "persist_active_workspace", None)
        if callable(persist):
            persist()

    def action_cursor_down(self) -> None:
        self.query_one("#ws-drawer-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#ws-drawer-list", ListView).action_cursor_up()

    def action_next_workspace(self) -> None:
        self._cycle_workspace(+1)

    def action_prev_workspace(self) -> None:
        self._cycle_workspace(-1)

    def _cycle_workspace(self, delta: int) -> None:
        cycle = getattr(self.app, "cycle_workspace", None)
        if callable(cycle):
            # Host pops this drawer, switches workspace, and re-pushes a fresh one.
            cycle(delta)

    def action_dismiss(self) -> None:
        self.dismiss()
