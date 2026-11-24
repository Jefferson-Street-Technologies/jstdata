"""Read-only modal for a historical action-log artifact."""

from __future__ import annotations

import json
from typing import Any, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from ..ops.discovery import inspect_event


class ArtifactModal(ModalScreen[None]):
    """Show JSON for a historical event + artifact (no Data API call)."""

    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
        Binding("q", "close", "Close", show=False),
    ]

    CSS = """
    ArtifactModal {
        align: center middle;
    }
    #artifact-panel {
        width: 90%;
        height: 80%;
        border: solid $accent;
        background: $surface;
        padding: 1;
    }
    #artifact-title {
        text-style: bold;
        padding-bottom: 1;
    }
    """

    def __init__(self, event_id: str, *, app_dir=None) -> None:
        super().__init__()
        self.event_id = event_id
        self.app_dir = app_dir
        self._body = "(loading)"

    def compose(self) -> ComposeResult:
        with Vertical(id="artifact-panel"):
            yield Static(f"Inspect {self.event_id}", id="artifact-title")
            with VerticalScroll():
                yield Static(self._body, id="artifact-body")
            yield Footer()

    def on_mount(self) -> None:
        result = inspect_event(self.event_id, app_dir=self.app_dir)
        payload: Any
        if result.status == "error":
            payload = {"error": result.error}
        else:
            payload = result.data
        text = json.dumps(payload, indent=2, default=str)
        # Keep modal responsive for large query artifacts.
        if len(text) > 40_000:
            text = text[:40_000] + "\n… truncated …"
        self.query_one("#artifact-body", Static).update(text)

    def action_close(self) -> None:
        self.dismiss(None)
