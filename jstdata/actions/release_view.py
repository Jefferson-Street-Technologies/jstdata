"""Release View — observations for a filing / release via ``GET /query``.

Idle until ``r``. Input Context should carry ``release`` (one id). Load
filters ``/query`` by ``release_id`` and lists each metric with its value
and units.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import ResearchContext, Workspace
from ..models import TimeSeries
from .base import ActionBinding, ActionSpec, label_for, register
from .query_view import format_value
from .screen import BaseScreen


@dataclass(frozen=True)
class ReleaseMetricRow:
    metric_id: str
    metric_label: str
    value: Optional[float]
    units: str
    entity_id: Optional[str] = None
    entity_label: Optional[str] = None
    series_id: Optional[str] = None


def rows_from_timeseries(
    results: list[TimeSeries],
    *,
    labels: Optional[dict[str, str]] = None,
) -> list[ReleaseMetricRow]:
    """One row per series: metric, last in-window value, units.

    Prefer the series label from ``/query`` over the app label cache.
    Metric ids are shared across entities; caching ``"X of Genomics"`` under
    ``accounts-payable-current`` would otherwise leak into a Tronox release.
    """
    labels = labels or {}
    rows: list[ReleaseMetricRow] = []
    for ts in results:
        series = ts.series
        obs = ts.observations[-1] if ts.observations else None
        entity = series.entities[0] if series.entities else None
        mid = series.metric_id or series.id
        # Series label is entity-specific; never prefer the metric-id cache.
        metric_label = series.label or mid
        entity_label = None
        if entity is not None:
            entity_label = entity.label or labels.get(entity.id) or entity.id
        rows.append(
            ReleaseMetricRow(
                metric_id=mid,
                metric_label=metric_label,
                value=obs.value if obs is not None else None,
                units=series.units or "",
                entity_id=entity.id if entity else None,
                entity_label=entity_label,
                series_id=series.id,
            )
        )
    rows.sort(key=lambda r: (r.metric_label or r.metric_id).lower())
    return rows


class ReleaseMetricItem(ListItem):
    """One metric card: full-width name, then value · units."""

    def __init__(self, row: ReleaseMetricRow) -> None:
        super().__init__()
        self.row = row

    def compose(self) -> ComposeResult:
        name = self.row.metric_label or self.row.metric_id
        value = format_value(self.row.value)
        units = self.row.units or "—"
        with Vertical(classes="rel-metric-card"):
            yield Label(name, classes="rel-metric-name")
            with Horizontal(classes="rel-metric-detail"):
                yield Label(value, classes="rel-metric-value")
                yield Label(f"  ·  {units}", classes="rel-metric-units")


class ReleaseScreen(BaseScreen):
    """Load a release's observations via ``/query`` and list metrics."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    DEFAULT_CSS = """
    #release-body { height: 1fr; padding: 1 3 0 3; }
    #release-kicker { color: #4ade80; text-style: bold; height: 1; }
    #release-title { color: #e0e0e0; text-style: bold; height: 1; margin-bottom: 1; }
    #release-meta { color: #888; height: auto; margin-bottom: 1; }
    #release-status { color: #888; height: 1; margin-bottom: 1; }
    #release-header {
        color: #4ade80;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
        display: none;
    }
    #release-list {
        height: 1fr;
        background: #0a0a0a;
        padding: 0 0 1 0;
    }
    ReleaseMetricItem {
        height: 4;
        min-height: 4;
        max-height: 4;
        width: 100%;
        padding: 0 2;
        margin: 0 0 1 0;
        border-left: solid #222;
        background: #0f0f0f;
    }
    ListView > ListItem.--highlight {
        background: #111a14;
        border-left: solid #4ade80;
    }
    .rel-metric-card {
        width: 100%;
        height: 1fr;
        padding: 0 0;
    }
    .rel-metric-name {
        width: 100%;
        color: #e0e0e0;
        text-style: bold;
        height: 1;
        margin-top: 0;
    }
    .rel-metric-detail {
        width: 100%;
        height: 1;
        margin-top: 1;
    }
    .rel-metric-value { width: auto; color: #86efac; height: 1; }
    .rel-metric-units { width: 1fr; color: #888; height: 1; }
    ListView > ListItem.--highlight .rel-metric-name { color: #f5f5f5; }
    ListView > ListItem.--highlight .rel-metric-value { color: #bbf7d0; }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        input_context: Optional[ResearchContext] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self._release_id: Optional[str] = None
        self._rows: list[ReleaseMetricRow] = []
        self._loaded = False

    def _resolved_input_context(self) -> ResearchContext:
        if self._input_context is not None:
            return self._input_context
        app_ctx = getattr(self.app, "input_context", None)
        if isinstance(app_ctx, ResearchContext):
            return app_ctx
        return ResearchContext.from_workspace(self.workspace)

    def compose_body(self) -> ComposeResult:
        with Vertical(id="release-body"):
            yield Label("RELEASE", id="release-kicker")
            yield Label("", id="release-title")
            yield Static("", id="release-meta", markup=True)
            yield Static(
                "Idle — press [bold]r[/bold] to load observations",
                id="release-status",
                markup=True,
            )
            yield Label("METRICS", id="release-header")
            yield ListView(id="release-list")

    def on_mount(self) -> None:
        super().on_mount()
        ctx = self._resolved_input_context()
        if not ctx.release:
            self.query_one("#release-title", Label).update(
                "No release in Input Context"
            )
            self.query_one("#release-meta", Static).update(
                "Drill into a table cell that has a release_id, "
                "open a Recent release, or set context.release in YAML."
            )
            self.query_one("#release-status", Static).update("")
            return
        self._release_id = ctx.release[0]
        labels = getattr(self.app, "labels", {}) or {}
        title = label_for(labels, self._release_id) or self._release_id
        self.query_one("#release-kicker", Label).update(
            f"RELEASE / {self._release_id}"
        )
        self.query_one("#release-title", Label).update(title)
        bits: list[str] = []
        if ctx.entity:
            bits.append(f"entity  {', '.join(ctx.entity[:3])}")
        if ctx.metric:
            bits.append(
                f"context metric  {', '.join(ctx.metric[:3])}  "
                "(load still returns all metrics on this release)"
            )
        self.query_one("#release-meta", Static).update(
            "\n".join(bits) if bits else "Filter: release_id only · /query"
        )
        self.query_one("#release-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold #e0e0e0]r[/] [#888]load[/]"]
        if self._loaded:
            keys.append("[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]move[/]")
        return keys

    def action_load(self) -> None:
        if not self._release_id:
            self.notify("No release in Input Context", severity="warning")
            return
        self.load_release()

    def action_cursor_down(self) -> None:
        self.query_one("#release-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#release-list", ListView).action_cursor_up()

    def _query_kwargs(self) -> dict[str, Any]:
        """``/query`` args: release_id (+ optional entity scope from context)."""
        ctx = self._resolved_input_context()
        kwargs: dict[str, Any] = {
            "release_id": [self._release_id],
            "tail": 1,
            "limit": 50,
        }
        if ctx.entity:
            kwargs["entity"] = list(ctx.entity)
        return kwargs

    def _render_rows(self, rows: list[ReleaseMetricRow]) -> None:
        list_view = self.query_one("#release-list", ListView)
        list_view.clear()
        header = self.query_one("#release-header", Label)
        if not rows:
            header.display = False
            list_view.append(
                ListItem(Label("No observations for this release_id."))
            )
            return
        header.display = True
        for row in rows:
            list_view.append(ReleaseMetricItem(row))
        list_view.index = 0
        list_view.focus()

    @work(exclusive=True, group="release-view")
    async def load_release(self) -> None:
        release_id = self._release_id
        if not release_id:
            return
        status = self.query_one("#release-status", Static)
        status.update("Loading /query…")
        kwargs = self._query_kwargs()
        try:
            results = await asyncio.to_thread(self.client.query, **kwargs)
        except Exception as exc:
            status.update(f"Could not load release: {exc}")
            return
        if release_id != self._release_id:
            return
        labels = getattr(self.app, "labels", {}) or {}
        rows = rows_from_timeseries(results, labels=labels)
        self._rows = rows
        self._loaded = True
        self._render_rows(rows)
        # Cache entity labels only — never store series labels under metric ids
        # (those are entity-qualified and would poison later releases).
        for row in rows:
            if (
                hasattr(self.app, "remember_label")
                and row.entity_id
                and row.entity_label
            ):
                self.app.remember_label(row.entity_id, row.entity_label)
        n = len(rows)
        status.update(
            f"Loaded · {n} metric{'s' if n != 1 else ''} · "
            f"release_id={release_id}"
        )
        self.refresh_footer()


def create_release_screen(client, workspace, input_context=None, **kwargs):
    return ReleaseScreen(client, workspace, input_context=input_context)


RELEASE_VIEW = register(
    ActionSpec(
        id="release",
        name="Release",
        description=(
            "Observations for a release / filing via GET /query "
            "(release_id filter). Shows metric, value, and units. Idle until r."
        ),
        create_screen=create_release_screen,
        arguments=(),
        bindings=(
            ActionBinding("r", "load", "Load observations for this release_id"),
            ActionBinding("j / k", "cursor", "Move in the metric list"),
        ),
        example="jst action add release",
    )
)
