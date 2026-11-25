"""Compact query composition header — chips, scope, refresh, tabs."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, Static

from .ui_primitives import ActionButton, ChipRow, EnumSelect

FREQUENCIES: tuple[Optional[str], ...] = (
    None,
    "Daily",
    "Weekly",
    "Monthly",
    "Quarterly",
    "Annual",
    "Intraday",
)

OBS_MODES = ("most_recent", "earliest", "all")
OBS_LABELS = {
    "most_recent": "Most recent 20",
    "earliest": "Earliest 20",
    "all": "All available",
}


class QueryComposeHeader(Vertical):
    """Interactive composition strip above the results pane."""

    DEFAULT_CSS = """
    QueryComposeHeader {
        height: auto;
        max-height: 10;
        width: 100%;
        padding: 0 0 1 0;
        border-bottom: solid #222;
        margin-bottom: 0;
    }
    #qc-title-row { height: 1; margin-bottom: 0; }
    #qc-title { width: 1fr; color: #f5f5f5; text-style: bold; }
    #qc-badge { width: auto; color: #86efac; }
    #qc-entities-label, #qc-metrics-label, #qc-series-label {
        color: #666;
        width: 14;
        height: 1;
    }
    #qc-entities-row, #qc-metrics-row, #qc-series-row {
        height: 1;
        margin-bottom: 0;
    }
    #qc-scope-row { height: 1; margin-top: 0; }
    #qc-refresh-row {
        height: 1;
        margin-top: 0;
        display: none;
    }
    #qc-refresh-row.-show { display: block; }
    #qc-refresh {
        width: 1fr;
        height: 1;
        color: #fbbf24;
    }
    #qc-refresh-btn { display: none; }
    #qc-refresh-row.-show #qc-refresh-btn { display: block; }
    #qc-coverage { height: 1; color: #888; }
    """

    class AddResource(Message):
        def __init__(self, kind: str) -> None:
            super().__init__()
            self.kind = kind

    class RemoveResource(Message):
        def __init__(self, kind: str, resource_id: str) -> None:
            super().__init__()
            self.kind = kind
            self.resource_id = resource_id

    class ScopeChanged(Message):
        def __init__(self, frequency: Optional[str], obs_mode: str) -> None:
            super().__init__()
            self.frequency = frequency
            self.obs_mode = obs_mode

    class Refresh(Message):
        pass

    def compose(self) -> ComposeResult:
        with Horizontal(id="qc-title-row"):
            yield Label("", id="qc-title")
            yield Label("", id="qc-badge")
        with Horizontal(id="qc-entities-row"):
            yield Label("Entities", id="qc-entities-label")
            yield ChipRow(
                "entity",
                add_label="+ Add entity",
                add_shortcut="E",
                id="qc-entities",
            )
        with Horizontal(id="qc-metrics-row"):
            yield Label("Metrics", id="qc-metrics-label")
            yield ChipRow(
                "metric",
                add_label="+ Add metric",
                add_shortcut="M",
                id="qc-metrics",
            )
        with Horizontal(id="qc-series-row"):
            yield Label("Direct series", id="qc-series-label")
            yield ChipRow(
                "series",
                add_label="+ Add series",
                add_shortcut="S",
                id="qc-series",
            )
        with Horizontal(id="qc-scope-row"):
            yield EnumSelect(
                FREQUENCIES,
                labels={None: "Any frequency"},
                value=None,
                id="qc-freq",
            )
            yield EnumSelect(
                list(OBS_MODES),
                labels=OBS_LABELS,
                value="most_recent",
                id="qc-obs",
            )
        with Horizontal(id="qc-refresh-row"):
            yield Static("", id="qc-refresh", markup=True)
            yield ActionButton(
                "Refresh",
                action_id="refresh",
                shortcut="r",
                variant="secondary",
                id="qc-refresh-btn",
            )
        yield Static("", id="qc-coverage", markup=True)

    def paint(
        self,
        *,
        title: str,
        badge: str,
        entities: Sequence[tuple[str, str]],
        metrics: Sequence[tuple[str, str]],
        series: Sequence[tuple[str, str]],
        frequency: Optional[str],
        obs_mode: str,
        mode: str = "chart",
        needs_refresh: bool,
        coverage: str = "",
    ) -> None:
        del mode  # View tabs live above the chart pane, not here.
        self.query_one("#qc-title", Label).update(title or "Untitled query")
        self.query_one("#qc-badge", Label).update(badge)
        self.query_one("#qc-entities", ChipRow).set_items(entities)
        self.query_one("#qc-metrics", ChipRow).set_items(metrics)
        self.query_one("#qc-series", ChipRow).set_items(series)
        freq = self.query_one("#qc-freq", EnumSelect)
        if frequency in FREQUENCIES:
            freq.set_value(frequency)
        else:
            freq.set_value(None)
        obs = self.query_one("#qc-obs", EnumSelect)
        if obs_mode in OBS_MODES:
            obs.set_value(obs_mode)
        row = self.query_one("#qc-refresh-row")
        banner = self.query_one("#qc-refresh", Static)
        if needs_refresh:
            banner.update("Composition changed — refresh to update chart")
            row.add_class("-show")
        else:
            banner.update("")
            row.remove_class("-show")
        self.query_one("#qc-coverage", Static).update(coverage)

    def on_chip_row_add(self, event: ChipRow.Add) -> None:
        event.stop()
        self.post_message(self.AddResource(event.kind))

    def on_chip_row_removed(self, event: ChipRow.Removed) -> None:
        event.stop()
        self.post_message(self.RemoveResource(event.kind, event.resource_id))

    def on_enum_select_changed(self, event: EnumSelect.Changed) -> None:
        event.stop()
        freq = self.query_one("#qc-freq", EnumSelect).value
        obs = self.query_one("#qc-obs", EnumSelect).value
        self.post_message(self.ScopeChanged(freq, obs))

    def on_action_button_pressed(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "refresh":
            self.post_message(self.Refresh())
