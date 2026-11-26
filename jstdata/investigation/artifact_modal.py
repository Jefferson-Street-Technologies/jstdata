"""Read-only modal for a historical action-log artifact (progressive disclosure)."""

from __future__ import annotations

import json
from typing import Any, Optional

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from ..ops.discovery import inspect_event
from ..ops.event_present import format_event


class ArtifactModal(ModalScreen[None]):
    """Structured inspect: summary → sample rows → request details JSON."""

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
        border: solid #4ade80;
        background: #0e0e0e;
        padding: 1;
    }
    #artifact-title {
        text-style: bold;
        color: #f5f5f5;
        padding-bottom: 0;
        height: auto;
    }
    #artifact-meta {
        color: #737373;
        height: auto;
        margin-bottom: 1;
    }
    #artifact-summary {
        color: #e5e5e5;
        height: auto;
        margin-bottom: 1;
    }
    #artifact-preview {
        color: #e5e5e5;
        height: auto;
        margin-bottom: 1;
        background: #101a14;
        padding: 0 1;
    }
    #artifact-details-kicker {
        color: #86efac;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #artifact-body {
        color: #a3a3a3;
        height: auto;
    }
    """

    def __init__(self, event_id: str, *, app_dir=None) -> None:
        super().__init__()
        self.event_id = event_id
        self.app_dir = app_dir

    def compose(self) -> ComposeResult:
        with Vertical(id="artifact-panel"):
            yield Static("Inspect", id="artifact-title", markup=True)
            yield Static("", id="artifact-meta", markup=True)
            with VerticalScroll():
                yield Static("", id="artifact-summary", markup=True)
                yield Static("", id="artifact-preview", markup=True)
                yield Static(
                    "Request details",
                    id="artifact-details-kicker",
                    markup=False,
                )
                yield Static("(loading)", id="artifact-body")
            yield Footer()

    def on_mount(self) -> None:
        result = inspect_event(self.event_id, app_dir=self.app_dir)
        if result.status == "error":
            self.query_one("#artifact-title", Static).update(
                f"[bold #fbbf24]Inspect failed[/]  [dim]{escape(self.event_id)}[/]"
            )
            self.query_one("#artifact-body", Static).update(
                str(result.error or "Unknown error")
            )
            return

        payload: Any = result.data if result.data is not None else {}
        # inspect_event returns the event dict (with artifact nested).
        event = payload if isinstance(payload, dict) else {"event_id": self.event_id}
        artifact_data = None
        art = event.get("artifact") if isinstance(event, dict) else None
        if isinstance(art, dict):
            artifact_data = art.get("data")

        presented = format_event(event, artifact_data=artifact_data)
        self.query_one("#artifact-title", Static).update(
            f"[bold #f5f5f5]{escape(presented.title)}[/]"
        )
        actor = presented.actor_label
        if actor.startswith("Agent"):
            actor_style = "#c4b5fd"
        elif actor == "You":
            actor_style = "#86efac"
        elif actor == "CLI":
            actor_style = "#fcd34d"
        else:
            actor_style = "#fda4af"
        self.query_one("#artifact-meta", Static).update(
            f"[dim]{escape(presented.when)}[/] · "
            f"[bold {actor_style}]{escape(actor)}[/] · "
            f"[dim]{escape(presented.operation)} · {escape(presented.event_id)}[/]"
        )

        summary_bits = list(presented.secondary_lines)
        if presented.stats_line:
            summary_bits.append(presented.stats_line)
        if presented.error:
            summary_bits.append(presented.error)
        summary = "\n".join(f"• {escape(line)}" for line in summary_bits) or (
            "[dim]No summary[/]"
        )
        self.query_one("#artifact-summary", Static).update(summary)

        if presented.preview_rows:
            rows = ["[bold #86efac]Sample observations[/]"]
            for row in presented.preview_rows:
                rows.append(
                    f"  {escape(row.label[:28]):<28}  "
                    f"{escape(row.period):<10}  {escape(row.value)}"
                )
            self.query_one("#artifact-preview", Static).update("\n".join(rows))
            self.query_one("#artifact-preview", Static).display = True
        else:
            self.query_one("#artifact-preview", Static).update("")
            self.query_one("#artifact-preview", Static).display = False

        details = {
            "event": presented.details,
            "artifact": artifact_data,
        }
        text = json.dumps(details, indent=2, default=str)
        if len(text) > 40_000:
            text = text[:40_000] + "\n… truncated …"
        self.query_one("#artifact-body", Static).update(text)

    def action_close(self) -> None:
        self.dismiss(None)
