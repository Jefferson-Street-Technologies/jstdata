"""Compact query composition header — title, scope, refresh."""

from __future__ import annotations

from typing import Optional, Sequence

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Label, Static

from .ui_primitives import ActionButton, EnumSelect

FREQUENCIES: tuple[Optional[str], ...] = (
    None,
    "Daily",
    "Weekly",
    "Monthly",
    "Quarterly",
    "Annual",
    "Intraday",
)

# Sentinel for variants/series whose frequency is null / blank.
NULL_FREQUENCY = "?"

KNOWN_FREQUENCIES: tuple[str, ...] = tuple(
    f for f in FREQUENCIES if isinstance(f, str)
)

OBS_MODES = ("most_recent", "earliest", "all")
OBS_LABELS = {
    "most_recent": "Most recent observations",
    "earliest": "Earliest observations",
    "all": "All available",
}


def normalize_variant_frequency(raw: Optional[str]) -> str:
    """Map a catalog frequency onto a selector token (``?`` when missing)."""
    text = (raw or "").strip()
    return text if text else NULL_FREQUENCY


def frequency_options_for(available: set[str]) -> list[Optional[str]]:
    """``Any`` plus frequencies present on variants, ``?`` last when needed."""
    options: list[Optional[str]] = [None]
    for freq in KNOWN_FREQUENCIES:
        if freq in available:
            options.append(freq)
    extras = sorted(
        f
        for f in available
        if f not in KNOWN_FREQUENCIES and f != NULL_FREQUENCY
    )
    options.extend(extras)
    if NULL_FREQUENCY in available:
        options.append(NULL_FREQUENCY)
    return options


class QueryComposeHeader(Vertical):
    """Title / scope strip above the results pane (composition lives in sidebar)."""

    DEFAULT_CSS = """
    QueryComposeHeader {
        height: auto;
        max-height: 8;
        width: 100%;
        background: #0a0a0a;
        padding: 0 0 1 0;
        border-bottom: solid #1f1f1f;
        margin-bottom: 0;
    }
    #qc-title-row {
        height: 1;
        margin: 1 0 0 0;
        align: left middle;
    }
    #qc-title {
        width: 1fr;
        height: 1;
        color: #fafafa;
        text-style: bold;
        content-align: left middle;
    }
    #qc-badge {
        width: auto;
        height: 1;
        color: #737373;
        content-align: left middle;
        margin-right: 1;
    }
    #qc-badge.-saved {
        color: #86efac;
    }
    #qc-subtitle {
        height: 1;
        color: #a3a3a3;
        margin: 0 0 0 0;
    }
    #qc-scope-row {
        height: 1;
        margin-top: 0;
        background: #0a0a0a;
        align: left middle;
    }
    #qc-scope-row EnumSelect {
        background: #0a0a0a;
        border: none;
        color: #a3a3a3;
        padding: 0 1 0 0;
    }
    #qc-refresh-row {
        height: 1;
        margin-top: 0;
        display: none;
        background: #0a0a0a;
    }
    #qc-refresh-row.-show { display: block; }
    #qc-refresh {
        width: 1fr;
        height: 1;
        color: #fbbf24;
    }
    #qc-refresh-btn { display: none; }
    #qc-refresh-row.-show #qc-refresh-btn { display: block; }
    #qc-coverage {
        height: 1;
        color: #737373;
        margin-top: 0;
        background: #0a0a0a;
    }
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

    class Rename(Message):
        """Kept for callers; rename lives on the sidebar query ··· menu."""

    def compose(self) -> ComposeResult:
        with Horizontal(id="qc-title-row"):
            yield Label("", id="qc-title")
            yield Label("", id="qc-badge")
        yield Label("", id="qc-subtitle")
        with Horizontal(id="qc-scope-row"):
            yield EnumSelect(
                [None],
                labels={None: "Any frequency", NULL_FREQUENCY: "?"},
                value=None,
                id="qc-freq",
                prefix="Frequency",
            )
            yield EnumSelect(
                list(OBS_MODES),
                labels=OBS_LABELS,
                value="most_recent",
                id="qc-obs",
                prefix="Data range",
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
        entities: Sequence[tuple[str, str]] = (),
        metrics: Sequence[tuple[str, str]] = (),
        series: Sequence[tuple[str, str]] = (),
        frequency: Optional[str],
        obs_mode: str,
        mode: str = "chart",
        needs_refresh: bool,
        coverage: str = "",
        subtitle: str = "",
        available_frequencies: Optional[Sequence[Optional[str]]] = None,
    ) -> None:
        from rich.markup import escape

        del mode  # View mode is painted by QueryScreen tabs.
        display_title = (title or "Untitled query").strip()
        self.query_one("#qc-title", Label).update(
            escape(display_title.upper())
        )
        badge_label = self.query_one("#qc-badge", Label)
        badge_label.update(escape(badge))
        badge_label.set_class(badge.startswith("✓"), "-saved")
        # Prefer explicit subtitle; else derive from composition chips.
        if not subtitle:
            from .naming import composition_subtitle

            subtitle = composition_subtitle(
                [m for m, _ in metrics],
                [e for e, _ in entities],
                [s for s, _ in series],
            )
        self.query_one("#qc-subtitle", Label).update(escape(subtitle))
        freq = self.query_one("#qc-freq", EnumSelect)
        if available_frequencies is not None:
            options = list(available_frequencies) or [None]
            # Keep a currently-selected frequency visible even if stale.
            if frequency is not None and frequency not in options:
                options.append(frequency)
            freq.set_options(
                options,
                labels={None: "Any frequency", NULL_FREQUENCY: "?"},
                value=frequency if frequency in options else None,
            )
        elif frequency is not None:
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

    def on_enum_select_changed(self, event: EnumSelect.Changed) -> None:
        event.stop()
        freq = self.query_one("#qc-freq", EnumSelect).value
        obs = self.query_one("#qc-obs", EnumSelect).value
        self.post_message(self.ScopeChanged(freq, obs))

    def on_action_button_pressed(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "refresh":
            self.post_message(self.Refresh())
