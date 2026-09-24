"""Shared help modal for action keybindings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label


class HelpScreen(ModalScreen):
    """Action-specific keybindings from the current ActionSpec."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }

    #help-container {
        width: 60;
        height: auto;
        max-height: 90%;
        border: thick #4ade80;
        background: #111111;
        padding: 1 2;
    }

    #help-title {
        text-style: bold;
        color: #4ade80;
        margin-bottom: 1;
        text-align: center;
    }

    #help-note {
        color: #888;
        margin-bottom: 1;
        text-align: center;
    }

    .key-row {
        height: auto;
        margin-bottom: 0;
    }

    .key-col {
        color: #4ade80;
        text-style: bold;
        width: 16;
    }

    .desc-col {
        color: #e0e0e0;
        width: 1fr;
    }

    #help-footer {
        color: #888;
        margin-top: 1;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("question_mark", "dismiss", "Close", show=False),
    ]

    def __init__(self, action_name: str, bindings: list) -> None:
        super().__init__()
        self.action_name = action_name
        self.action_bindings = bindings

    def compose(self) -> ComposeResult:
        with Vertical(id="help-container"):
            yield Label(f"{self.action_name.upper()} // KEYBINDINGS", id="help-title")
            yield Label(
                "Investigation keys: : commands · n/p actions · N/P workspaces · q quit.",
                id="help-note",
            )
            if not self.action_bindings:
                yield Label("(no action-specific bindings)", classes="desc-col")
            else:
                for b in self.action_bindings:
                    if not getattr(b, "show_in_help", True):
                        continue
                    yield Horizontal(
                        Label(b.key, classes="key-col"),
                        Label(b.description, classes="desc-col"),
                        classes="key-row",
                    )
            yield Label(
                "[bold]esc[/bold] / [bold]?[/bold] close",
                id="help-footer",
                markup=True,
            )

    def action_dismiss(self) -> None:
        self.dismiss()
