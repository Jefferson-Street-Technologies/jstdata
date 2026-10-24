"""Home action — reorienting launcher for investigation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import Investigation, Workspace
from ..actions.base import ActionBinding, ActionSpec, register
from ..actions.screen import BaseScreen


@dataclass(frozen=True)
class HomeItem:
    """One launcher row on the home screen."""

    kind: str
    title: str
    detail: str
    payload: Any = None


def build_home_items(inv: Investigation) -> list[HomeItem]:
    """Core primitives + saved queries. Pure so tests don't need the TUI."""
    ws = inv.workspace
    items = [
        HomeItem(
            kind="search",
            title="SEARCH",
            detail="Catalog → collect into the workspace",
        ),
        HomeItem(
            kind="workspace",
            title="WORKSPACE",
            detail=(
                f"bag  metric={len(ws.metric)}  entity={len(ws.entity)}  "
                f"series={len(ws.series)}"
            ),
        ),
        HomeItem(
            kind="table",
            title="TABLE",
            detail="Latest-value matrix over the workspace",
        ),
        HomeItem(
            kind="query_new",
            title="QUERY",
            detail=(
                "Save the bag as a cartesian query and view JSON series"
                if not ws.is_empty()
                else "Stage ids first (Search), then save and run"
            ),
        ),
    ]
    for qid in sorted(inv.queries):
        query = inv.queries[qid]
        name = query.name or f"query {qid}"
        items.append(
            HomeItem(
                kind="query",
                title=f"Q{qid}",
                detail=(
                    f"{name}  metric={len(query.metric)} "
                    f"entity={len(query.entity)} series={len(query.series)}"
                ),
                payload=qid,
            )
        )
    return items


class PrimitiveRow(ListItem):
    def __init__(self, item: HomeItem) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        yield Label(f"{self.item.title:<10} {self.item.detail}")


class HomeScreen(BaseScreen):
    """Launcher that makes Search, Collect, Query, and Table one Enter away."""

    BINDINGS = [
        Binding("escape", "app.bell", " ", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("enter", "confirm", "Open primitive", priority=True),
    ]

    DEFAULT_CSS = """
    #home-body {
        height: 1fr;
    }
    .pane-container {
        border: solid #222;
        background: #0f0f0f;
        height: 1fr;
    }
    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    #home-kicker {
        color: #4ade80;
        text-style: bold;
        padding: 1 1 0 1;
        height: 1;
    }
    #home-title {
        color: #e0e0e0;
        text-style: bold;
        padding: 0 1;
        height: 1;
    }
    #home-orient {
        color: #888;
        padding: 0 1 1 1;
        height: auto;
        max-height: 4;
    }
    #home-list {
        height: 1fr;
        background: #0f0f0f;
    }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
    }
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
        self._items: list[HomeItem] = []

    def compose_body(self) -> ComposeResult:
        with Vertical(id="home-body"):
            with Vertical(classes="pane-container"):
                yield Label("HOME", classes="pane-header")
                yield Label("JEFFERSON STREET", id="home-kicker")
                yield Label(self.home_title, id="home-title")
                yield Static("", id="home-orient")
                yield ListView(id="home-list")

    def on_mount(self) -> None:
        super().on_mount()
        self._rebuild()
        self.query_one("#home-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold]j[/bold]/[bold]k[/bold] move",
            "[bold]enter[/bold] open",
            "[bold]g[/bold] home",
        ]

    def footer_status(self) -> list[str]:
        parts = super().footer_status()
        ws = self.workspace
        label = ws.label or "workspace"
        parts.append(label)
        return parts

    def _rebuild(self) -> None:
        inv = getattr(self.app, "investigation", None)
        notes = ""
        if isinstance(inv, Investigation):
            self._items = build_home_items(inv)
            notes = (inv.notes or "").strip().splitlines()[0] if inv.notes else ""
        else:
            self._items = build_home_items(
                Investigation.new("untitled", title=self.home_title)
            )
        orient_bits = []
        if notes:
            orient_bits.append(notes)
        if self.body:
            first = self.body.strip().splitlines()[0]
            if first and first not in orient_bits:
                orient_bits.append(first)
        orient_bits.append(
            "Search · Workspace · Table · Query — Enter to open · g always returns here"
        )
        self.query_one("#home-orient", Static).update("\n".join(orient_bits))
        list_view = self.query_one("#home-list", ListView)
        list_view.clear()
        for item in self._items:
            list_view.append(PrimitiveRow(item))

    def action_cursor_down(self) -> None:
        lv = self.query_one("#home-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#home-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    def _highlighted_item(self) -> Optional[HomeItem]:
        child = self.query_one("#home-list", ListView).highlighted_child
        if isinstance(child, PrimitiveRow):
            return child.item
        return None

    def action_confirm(self) -> None:
        item = self._highlighted_item()
        if item is None:
            self.notify("Nothing highlighted", severity="warning")
            return
        launch = getattr(self.app, "launch_home_item", None)
        if launch is None:
            self.notify("Host cannot launch primitives", severity="error")
            return
        launch(item)

    @on(ListView.Selected, "#home-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, PrimitiveRow):
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
            "Reorienting launcher for Search, Workspace, Table, and Query. "
            "g always returns here."
        ),
        create_screen=create_home_screen,
        arguments=(),
        bindings=(
            ActionBinding("j / k", "cursor", "Move in the primitive list"),
            ActionBinding("enter", "confirm", "Open the highlighted primitive"),
            ActionBinding("g", "go_home", "Return to home (from any view)"),
        ),
        example="jst open tutorial",
    )
)
