"""Drill-down chooser modal — pick a child View for the current selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from ..investigation.model import ResearchContext


@dataclass
class DrillOption:
    """One chooser row → ``open_child_view`` payload."""

    title: str
    detail: str
    action_type: str
    context: ResearchContext
    description: Optional[str] = None
    args: dict = field(default_factory=dict)


class DrillOptionRow(ListItem):
    def __init__(self, option: DrillOption) -> None:
        super().__init__()
        self.option = option

    def compose(self) -> ComposeResult:
        yield Label(f"{self.option.title}\n  {self.option.detail}")


class DrillDownModal(ModalScreen[Optional[DrillOption]]):
    """Compact list of drill targets. Enter confirms; Esc cancels."""

    DEFAULT_CSS = """
    DrillDownModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #drill-box {
        width: 72;
        max-height: 24;
        height: auto;
        border: thick #4ade80;
        background: #111111;
        padding: 1 1;
    }
    #drill-title {
        color: #4ade80;
        text-style: bold;
        margin-bottom: 1;
        padding: 0 1;
    }
    #drill-hint {
        color: #888;
        padding: 0 1;
        margin-bottom: 1;
    }
    #drill-list {
        height: auto;
        max-height: 16;
        background: #0a0a0a;
    }
    ListView > ListItem {
        background: transparent;
        color: #e0e0e0;
        padding: 0 1;
        height: auto;
    }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "confirm", "Open", show=False, priority=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, options: list[DrillOption], *, heading: str = "DRILL DOWN") -> None:
        super().__init__()
        self.options = list(options)
        self.heading = heading

    def compose(self) -> ComposeResult:
        with Vertical(id="drill-box"):
            yield Label(self.heading, id="drill-title")
            yield Static(
                "[bold]enter[/bold] open  ·  [bold]esc[/bold] cancel",
                id="drill-hint",
                markup=True,
            )
            yield ListView(id="drill-list")

    def on_mount(self) -> None:
        lv = self.query_one("#drill-list", ListView)
        for opt in self.options:
            lv.append(DrillOptionRow(opt))
        if self.options:
            lv.index = 0
        lv.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        lv = self.query_one("#drill-list", ListView)
        child = lv.highlighted_child
        if isinstance(child, DrillOptionRow):
            self.dismiss(child.option)
            return
        if self.options:
            self.dismiss(self.options[0])

    def action_cursor_down(self) -> None:
        lv = self.query_one("#drill-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#drill-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    @on(ListView.Selected, "#drill-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, DrillOptionRow):
            self.dismiss(event.item.option)
