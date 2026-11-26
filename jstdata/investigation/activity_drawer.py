"""Right drawer listing recent action-log events for the open project."""

from __future__ import annotations

from typing import Any, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from ..ops.runner import get_store


class _EventItem(ListItem):
    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        eid = event.get("event_id") or "?"
        op = event.get("operation") or "?"
        status = event.get("status") or "?"
        when = (event.get("completed_at") or "")[:19]
        label = f"{when}  {status:<7}  {op}  {eid}"
        super().__init__(Label(label))


class ActivityDrawer(ModalScreen[Optional[str]]):
    """Right-side action-log drawer; Enter returns the selected event id."""

    BINDINGS = [
        Binding("escape", "dismiss_drawer", "Close", show=True),
        Binding("enter", "open_event", "Inspect historical", show=True),
        Binding("r", "rerun_event", "Rerun against current data", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    CSS = """
    ActivityDrawer {
        align: right middle;
        background: transparent;
    }
    #activity-panel {
        width: 48;
        height: 100%;
        border-left: solid #2a2a2a;
        background: #0e0e0e;
        padding: 1 1;
    }
    #activity-title {
        text-style: bold;
        color: #a8a8a8;
        height: 1;
        margin-bottom: 1;
    }
    #activity-list {
        height: 1fr;
        background: transparent;
        border: none;
    }
    """

    def __init__(
        self,
        *,
        project_id: Optional[str],
        investigation_id: Optional[str],
        app_dir=None,
        limit: int = 40,
    ) -> None:
        super().__init__()
        self.project_id = project_id
        self.investigation_id = investigation_id
        self.app_dir = app_dir
        self.limit = limit
        self._events: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="activity-panel"):
            scope = self.investigation_id or self.project_id or "global"
            yield Static(f"Activity — {scope}", id="activity-title")
            yield ListView(id="activity-list")
            yield Footer()

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        store = get_store(app_dir=self.app_dir)
        self._events = store.list_events(
            project_id=self.project_id,
            investigation_id=self.investigation_id,
            limit=self.limit,
        )
        listing = self.query_one("#activity-list", ListView)
        listing.clear()
        if not self._events:
            listing.append(ListItem(Label("(no events yet)")))
            return
        for event in self._events:
            listing.append(_EventItem(event))
        listing.index = 0

    def action_dismiss_drawer(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        listing = self.query_one("#activity-list", ListView)
        listing.action_cursor_down()

    def action_cursor_up(self) -> None:
        listing = self.query_one("#activity-list", ListView)
        listing.action_cursor_up()

    def action_open_event(self) -> None:
        listing = self.query_one("#activity-list", ListView)
        item = listing.highlighted_child
        if isinstance(item, _EventItem):
            self.dismiss({"event_id": item.event.get("event_id"), "mode": "inspect"})
            return
        self.dismiss(None)

    def action_rerun_event(self) -> None:
        listing = self.query_one("#activity-list", ListView)
        item = listing.highlighted_child
        if isinstance(item, _EventItem):
            self.dismiss({"event_id": item.event.get("event_id"), "mode": "rerun"})
            return
        self.dismiss(None)

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _EventItem):
            self.dismiss(
                {"event_id": item.event.get("event_id"), "mode": "inspect"}
            )
