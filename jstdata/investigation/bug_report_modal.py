"""Bug report popup (! / Shift+1) — free-text + data replication snapshot."""

from __future__ import annotations

from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static


class BugReportModal(ModalScreen[Optional[str]]):
    """Collect a short note; host attaches the screen replication payload."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "submit", "Submit", show=True, priority=True),
    ]

    CSS = """
    BugReportModal { align: center middle; }
    #br-panel {
        width: 72;
        height: auto;
        max-height: 70%;
        border: solid #fbbf24;
        background: #0c0c0c;
        padding: 1 2;
    }
    #br-title { text-style: bold; color: #fbbf24; height: 1; margin-bottom: 1; }
    #br-hint { color: #888; height: auto; margin-bottom: 1; }
    #br-context { color: #a8a8a8; height: auto; max-height: 8; margin-bottom: 1; }
    #br-input {
        margin-bottom: 1;
    }
    """

    def __init__(self, *, context_summary: str = "") -> None:
        super().__init__()
        self._context_summary = context_summary

    def compose(self) -> ComposeResult:
        with Vertical(id="br-panel"):
            yield Static("Bug report", id="br-title")
            yield Static(
                "Describe what went wrong. Screen data needed to re-run the "
                "same API calls is captured automatically.  Enter to save · Esc cancel",
                id="br-hint",
            )
            if self._context_summary:
                yield Static(self._context_summary, id="br-context", markup=True)
            yield Input(placeholder="What happened?", id="br-input")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#br-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        text = self.query_one("#br-input", Input).value
        self.dismiss(text if text is not None else "")

    @on(Input.Submitted, "#br-input")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()
