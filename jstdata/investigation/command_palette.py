"""``:`` command palette — discovery, history, and dispatch."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from .commands import (
    CommandError,
    list_command_suggestions,
    parse_command_line,
)


class CommandPalette(ModalScreen[str | None]):
    """Transparent bottom command line. Returns the submitted line or None."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center bottom;
        background: transparent;
    }

    #cmd-panel {
        width: 100%;
        height: auto;
        max-height: 14;
        background: #111111;
        border-top: tall #4ade80;
        padding: 0 1 1 1;
    }

    #cmd-suggestions {
        height: auto;
        max-height: 8;
        color: #c0c0c0;
        padding: 0 1;
    }

    #cmd-error {
        color: #f87171;
        height: auto;
        padding: 0 1;
    }

    #cmd-input {
        width: 100%;
        background: #0a0a0a;
        border: solid #333;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
        Binding("up", "history_prev", "History", show=False),
        Binding("down", "history_next", "History", show=False),
    ]

    def __init__(self, history: list[str] | None = None) -> None:
        super().__init__()
        self.history = list(history or [])
        self._hist_index: int | None = None
        self._draft = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="cmd-panel"):
            yield Static(self._format_suggestions(""), id="cmd-suggestions")
            yield Static("", id="cmd-error")
            yield Input(placeholder=": workspace | search | add | rm", id="cmd-input")

    def on_mount(self) -> None:
        inp = self.query_one("#cmd-input", Input)
        inp.value = ""
        inp.focus()

    def _format_suggestions(self, text: str) -> str:
        # First token only for catalog filter
        stripped = text.strip()
        if stripped.startswith(":"):
            stripped = stripped[1:].lstrip()
        prefix = stripped.split(" ", 1)[0] if stripped else ""
        # If user already typed a known command + space, show usage
        parts = stripped.split(None, 1)
        specs = list_command_suggestions(prefix if len(parts) <= 1 else parts[0])
        if len(parts) >= 1 and parts[0] in {s.name for s in list_command_suggestions()}:
            from .commands import COMMANDS

            match = next((c for c in COMMANDS if c.name == parts[0]), None)
            if match:
                return match.help_line()
        if not specs:
            return "(no matching commands)"
        return "\n".join(s.help_line() for s in specs)

    @on(Input.Changed, "#cmd-input")
    def on_changed(self, event: Input.Changed) -> None:
        self.query_one("#cmd-suggestions", Static).update(
            self._format_suggestions(event.value)
        )
        self.query_one("#cmd-error", Static).update("")
        self._hist_index = None

    @on(Input.Submitted, "#cmd-input")
    def on_submit(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            self.dismiss(None)
            return
        try:
            parse_command_line(raw)  # validate before dismiss
        except CommandError as exc:
            self.query_one("#cmd-error", Static).update(str(exc))
            return
        line = raw if raw.startswith(":") else f":{raw}"
        # store without leading colon for history display consistency
        hist = raw[1:].strip() if raw.startswith(":") else raw
        self.dismiss(hist)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_history_prev(self) -> None:
        if not self.history:
            return
        inp = self.query_one("#cmd-input", Input)
        if self._hist_index is None:
            self._draft = inp.value
            self._hist_index = len(self.history) - 1
        elif self._hist_index > 0:
            self._hist_index -= 1
        inp.value = self.history[self._hist_index]
        inp.cursor_position = len(inp.value)

    def action_history_next(self) -> None:
        if self._hist_index is None:
            return
        inp = self.query_one("#cmd-input", Input)
        if self._hist_index >= len(self.history) - 1:
            self._hist_index = None
            inp.value = self._draft
        else:
            self._hist_index += 1
            inp.value = self.history[self._hist_index]
        inp.cursor_position = len(inp.value)
