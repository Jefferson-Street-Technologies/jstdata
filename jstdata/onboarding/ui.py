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


class TipHide(Static):
    """Clickable "✕ hide tips" line inside the tip box."""

    def on_click(self) -> None:
        set_tips = getattr(self.app, "set_tips", None)
        if callable(set_tips):
            set_tips(False)


class TipBox(Vertical):
    """Tip box docked across the top of the screen."""

    DEFAULT_CSS = """
    TipBox {
        dock: top;
        width: 100%;
        height: auto;
        max-height: 8;
        background: #14202b;
        border: round #38bdf8;
        border-title-color: #38bdf8;
        border-title-style: bold;
        padding: 0 1;
    }

    TipBox #tip-text {
        color: #cfe8f7;
        height: auto;
    }

    TipBox #tip-hide {
        width: 100%;
        content-align: right middle;
        color: #777;
    }

    TipBox #tip-hide:hover {
        color: #e0e0e0;
        text-style: underline;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self.border_title = "TIP"

    def compose(self) -> ComposeResult:
        yield Static(self._text, id="tip-text", markup=False)
        yield TipHide("✕ hide tips", id="tip-hide", markup=False)

    def on_mount(self) -> None:
        self._render_text()

    def update_text(self, text: str) -> None:
        self._text = text
        self._render_text()

    def _render_text(self) -> None:
        # Children may not exist yet right after mount; on_mount re-applies.
        for line in self.query("#tip-text").results(Static):
            line.update(self._text)


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
        self._render_text()

    def update_text(self, header: str, body: str, do: str) -> None:
        self._text = (header, body, do)
        self._render_text()

    def _render_text(self) -> None:
        # Children may not exist yet right after mount; on_mount re-applies.
        header, body, do = self._text
        for w in self.query("#coach-header").results(Label):
            w.update(header)
        for w in self.query("#coach-body").results(Static):
            w.update(body)
        for w in self.query("#coach-do").results(Static):
            w.update(do)
            w.display = bool(do)


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


# Results of the completion pop-up (besides a tutorial id for "next").
COMPLETE_MENU = "__menu__"
COMPLETE_BACK = "__back__"
COMPLETE_STAY = "__stay__"


class TutorialComplete(ModalScreen[str]):
    """Shown once when a tutorial's last step is done: what next?

    Dismisses with the next tutorial's id, ``COMPLETE_MENU``,
    ``COMPLETE_BACK`` (reopen the user's investigation), or ``COMPLETE_STAY``.
    """

    DEFAULT_CSS = """
    TutorialComplete {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #done-container {
        width: 90%;
        max-width: 80;
        height: auto;
        border: thick #4ade80;
        background: #111111;
        padding: 1 2;
    }

    #done-heading {
        text-style: bold;
        color: #4ade80;
    }

    #done-title {
        color: #e0e0e0;
        text-style: bold;
        margin-bottom: 1;
    }

    #done-text {
        width: 100%;
        color: #aaa;
        margin-bottom: 1;
    }

    #done-list {
        height: auto;
        background: #111111;
    }

    TutorialComplete ListView > TutorialRow {
        height: 2;
        padding: 0 1;
    }

    #done-footer {
        color: #888;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "stay", "Keep exploring"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, tutorial: Tutorial, message: str, next_tutorial: Optional[Tutorial]) -> None:
        super().__init__()
        self.tutorial = tutorial
        self.message = message
        self.next_tutorial = next_tutorial

    def compose(self) -> ComposeResult:
        with Vertical(id="done-container"):
            yield Label("✓ TUTORIAL COMPLETE", id="done-heading")
            yield Label(self.tutorial.title, id="done-title", markup=False)
            if self.message:
                yield Static(self.message, id="done-text", markup=False)
            rows: list[TutorialRow] = []
            if self.next_tutorial is not None:
                nxt = self.next_tutorial
                rows.append(
                    TutorialRow(nxt.id, f"→ Next: {nxt.title}", f"   {nxt.level_label}")
                )
            rows.append(TutorialRow(COMPLETE_MENU, "☰ All tutorials", "   Pick any tutorial."))
            rows.append(
                TutorialRow(
                    COMPLETE_BACK,
                    "← Back to my investigation",
                    "   Reopen what you had open before the tutorial.",
                )
            )
            rows.append(
                TutorialRow(
                    COMPLETE_STAY,
                    "Keep exploring here",
                    "   Stay in this tutorial's investigation, without the coach.",
                )
            )
            yield ListView(*rows, id="done-list")
            yield Label("enter choose · j/k move · esc keep exploring", id="done-footer")

    def on_mount(self) -> None:
        self.query_one("#done-list", ListView).focus()

    @on(ListView.Selected, "#done-list")
    def on_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, TutorialRow):
            self.dismiss(event.item.value)

    def action_cursor_down(self) -> None:
        self.query_one("#done-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#done-list", ListView).action_cursor_up()

    def action_stay(self) -> None:
        self.dismiss(COMPLETE_STAY)
