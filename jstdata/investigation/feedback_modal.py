"""Minimal feedback comment modal (F)."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static


class FeedbackModal(ModalScreen[Optional[str]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "submit", "Submit", show=True),
    ]

    CSS = """
    FeedbackModal { align: center middle; }
    #fb-panel {
        width: 70%;
        height: auto;
        max-height: 40%;
        border: solid #4ade80;
        background: #0c0c0c;
        padding: 1;
    }
    #fb-title { text-style: bold; height: 1; margin-bottom: 1; }
    #fb-hint { color: #666; height: 1; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="fb-panel"):
            yield Static("Send feedback", id="fb-title")
            yield Static(
                "What's wrong, confusing, or missing? Diagnostics captured.",
                id="fb-hint",
            )
            yield Input(placeholder="Your comment…", id="fb-input")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#fb-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        text = self.query_one("#fb-input", Input).value.strip()
        self.dismiss(text or None)
