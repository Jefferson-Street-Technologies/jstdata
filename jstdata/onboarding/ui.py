"""Onboarding chrome: the docked coach bar and the tutorial menu."""

from __future__ import annotations

from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from .tutorials import Tutorial

# Menu result when the user asks to leave the running tutorial.
LEAVE_TUTORIAL = "__leave__"


class CoachBar(Static):
    """Instruction strip docked to the top of the active action screen."""

    DEFAULT_CSS = """
    CoachBar {
        dock: top;
        height: auto;
        max-height: 12;
        background: #1a1a2e;
        border-bottom: thick #38bdf8;
        padding: 0 1;
    }

    CoachBar #coach-header {
        color: #38bdf8;
        text-style: bold;
    }

    CoachBar #coach-body {
        color: #e0e0e0;
        height: auto;
    }

    CoachBar #coach-do {
        color: #facc15;
        text-style: bold;
        height: auto;
    }

    CoachBar #coach-footer {
        color: #777;
        margin-bottom: 1;
    }
    """

    def __init__(self, header: str, body: str, do: str) -> None:
        super().__init__()
        self._text = (header, body, do)

    def compose(self) -> ComposeResult:
        header, body, do = self._text
        yield Label(header, id="coach-header", markup=False)
        yield Static(body, id="coach-body", markup=False)
        yield Static(do, id="coach-do", markup=False)
        yield Label(
            "tutorial menu / leave: press : then type tutorial (or t, except in a query)",
            id="coach-footer",
        )

    def on_mount(self) -> None:
        self.query_one("#coach-do", Static).display = bool(self._text[2])

    def update_text(self, header: str, body: str, do: str) -> None:
        self._text = (header, body, do)
        if not self.is_mounted:
            return
        self.query_one("#coach-header", Label).update(header)
        self.query_one("#coach-body", Static).update(body)
        do_line = self.query_one("#coach-do", Static)
        do_line.update(do)
        do_line.display = bool(do)


class TutorialRow(ListItem):
    def __init__(self, value: str, title: str, detail: str) -> None:
        super().__init__()
        self.value = value
        self.title = title
        self.detail = detail

    def compose(self) -> ComposeResult:
        yield Label(self.title, classes="tut-title", markup=False)
        yield Label(self.detail, classes="tut-detail", markup=False)


class TutorialMenu(ModalScreen[Optional[str]]):
    """Pick a tutorial. Dismisses with its id, ``LEAVE_TUTORIAL``, or None."""

    DEFAULT_CSS = """
    TutorialMenu {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #tut-container {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        border: thick #38bdf8;
        background: #111111;
        padding: 1 2;
    }

    #tut-heading {
        text-style: bold;
        color: #38bdf8;
        margin-bottom: 1;
    }

    #tut-intro {
        width: 100%;
        color: #aaa;
        margin-bottom: 1;
    }

    #tut-list {
        height: auto;
        max-height: 24;
        background: #111111;
    }

    TutorialMenu ListView > TutorialRow {
        height: 3;
        padding: 0 1;
    }

    TutorialRow.-highlighted {
        background: #1a2a3a;
    }

    .tut-title {
        color: #e0e0e0;
        text-style: bold;
    }

    .tut-detail {
        color: #888;
    }

    #tut-footer {
        color: #888;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("t", "close", "Close", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(
        self,
        tutorials: list[Tutorial],
        completed: set[str],
        *,
        first_run: bool = False,
        active_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.tutorials = tutorials
        self.completed = completed
        self.first_run = first_run
        self.active_id = active_id

    def compose(self) -> ComposeResult:
        with Vertical(id="tut-container"):
            yield Label("TUTORIALS", id="tut-heading")
            if self.first_run:
                intro = (
                    "Welcome to Jefferson Street. Pick a tutorial to learn by doing, "
                    "or press Esc to explore on your own. Press t (or type "
                    ":tutorial) any time to come back here."
                )
            else:
                intro = (
                    "Each tutorial runs in its own investigation; yours is reopened "
                    "when you leave. Levels go from every key named to open-ended."
                )
            yield Static(intro, id="tut-intro", markup=False)
            rows: list[TutorialRow] = []
            if self.active_id:
                rows.append(
                    TutorialRow(
                        LEAVE_TUTORIAL,
                        "← Leave current tutorial",
                        "   Go back to the investigation you had open before.",
                    )
                )
            for tut in self.tutorials:
                mark = "✓ " if tut.id in self.completed else "  "
                running = "  (running)" if tut.id == self.active_id else ""
                rows.append(
                    TutorialRow(
                        tut.id,
                        f"{mark}{tut.title}{running}",
                        f"   {tut.level_label} — {tut.summary}",
                    )
                )
            yield ListView(*rows, id="tut-list")
            yield Label("enter start · j/k move · esc close", id="tut-footer")

    def on_mount(self) -> None:
        self.query_one("#tut-list", ListView).focus()

    @on(ListView.Selected, "#tut-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TutorialRow):
            self.dismiss(event.item.value)

    def action_cursor_down(self) -> None:
        self.query_one("#tut-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#tut-list", ListView).action_cursor_up()

    def action_close(self) -> None:
        self.dismiss(None)
