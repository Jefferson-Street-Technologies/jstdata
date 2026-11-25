"""Bug report footer panel (! / Shift+1) — free-text + replication snapshot.

Expands under the main pane (not a modal) so the screen context stays visible.
"""

from __future__ import annotations

from typing import Any, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, Label, Static


class BugReportPanel(Vertical):
    """Collapsible footer form: describe the bug, Enter saves, Esc cancels."""

    DEFAULT_CSS = """
    BugReportPanel {
        display: none;
        height: auto;
        max-height: 10;
        width: 100%;
        background: #12100a;
        border-top: solid #fbbf24;
        padding: 1 1 0 1;
    }
    BugReportPanel.-open {
        display: block;
    }
    #br-title-row {
        height: 1;
        margin-bottom: 0;
    }
    #br-title {
        width: 1fr;
        color: #fbbf24;
        text-style: bold;
        height: 1;
    }
    #br-keys {
        width: auto;
        color: #888;
        height: 1;
    }
    #br-context {
        color: #a8a8a8;
        height: 1;
        margin-bottom: 0;
    }
    #br-input {
        margin: 0 0 1 0;
        background: #0a0a0a;
        border: tall #333;
    }
    #br-input:focus {
        border: tall #fbbf24;
    }
    """

    class Submitted(Message):
        def __init__(self, message: str) -> None:
            super().__init__()
            self.message = message

    class Cancelled(Message):
        pass

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(id="bug-report-panel", **kwargs)
        self._replication: dict[str, Any] = {}
        self._screen_name: str = ""

    @property
    def is_open(self) -> bool:
        return self.has_class("-open")

    @property
    def pending_replication(self) -> dict[str, Any]:
        return dict(self._replication)

    @property
    def pending_screen_name(self) -> str:
        return self._screen_name

    def compose(self) -> ComposeResult:
        with Horizontal(id="br-title-row"):
            yield Label("Bug report", id="br-title")
            yield Label("Enter save · Esc cancel", id="br-keys")
        yield Static("", id="br-context", markup=True)
        yield Input(placeholder="What happened?", id="br-input")

    def open_panel(
        self,
        *,
        context_summary: str,
        replication: dict[str, Any],
        screen_name: str,
    ) -> None:
        self._replication = dict(replication or {})
        self._screen_name = screen_name or ""
        ctx = self.query_one("#br-context", Static)
        if context_summary:
            ctx.update(context_summary)
            ctx.display = True
        else:
            ctx.update("")
            ctx.display = False
        inp = self.query_one("#br-input", Input)
        inp.value = ""
        self.add_class("-open")
        inp.focus()

    def close_panel(self) -> None:
        was_open = self.is_open
        self.remove_class("-open")
        self._replication = {}
        self._screen_name = ""
        try:
            self.query_one("#br-input", Input).value = ""
        except Exception:  # noqa: BLE001
            pass
        if was_open:
            self.post_message(self.Cancelled())

    def submit(self) -> None:
        if not self.is_open:
            return
        text = self.query_one("#br-input", Input).value
        message = text if text is not None else ""
        # Keep replication on the panel until the screen handler reads it.
        self.remove_class("-open")
        try:
            self.query_one("#br-input", Input).value = ""
        except Exception:  # noqa: BLE001
            pass
        self.post_message(self.Submitted(message))

    def action_cancel(self) -> None:
        self.close_panel()

    def action_submit(self) -> None:
        self.submit()

    @on(Input.Submitted, "#br-input")
    def _submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.submit()


# Back-compat alias for any leftover imports.
BugReportModal = BugReportPanel
