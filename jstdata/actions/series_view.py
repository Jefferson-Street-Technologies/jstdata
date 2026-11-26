"""Series View — metadata for one series; primary CTA is plot."""

from __future__ import annotations

from typing import Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, Static

from ..client import JSTDataClient
from ..investigation.model import ResearchContext, Workspace
from ..investigation.ui_primitives import ActionButton
from ..models import Series
from .base import ActionBinding, ActionSpec, register
from .screen import BaseScreen


class SeriesScreen(BaseScreen):
    """Show everything we know about a series; plot is the main action."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("enter", "plot", "Plot", priority=True),
        Binding("p", "plot", "Plot", show=False),
        Binding("e", "open_entity", "Open entity", show=False),
        Binding("m", "open_metric", "Open metric", show=False),
        Binding("space", "collect", "Collect series", show=False),
    ]

    DEFAULT_CSS = """
    #series-body { height: 1fr; padding: 1 2; }
    #series-title {
        color: #f5f5f5;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #series-id { color: #666; height: 1; margin-bottom: 1; }
    #series-fields {
        height: auto;
        color: #c0c0c0;
        margin-bottom: 1;
    }
    #series-actions { height: 1; margin: 1 0; }
    #series-status { color: #888; height: 1; }
    #series-links { height: 1; margin-top: 1; }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        input_context: Optional[ResearchContext] = None,
        series_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self._series_id = series_id
        self._series: Optional[Series] = None
        self._loaded = False

    def compose_body(self) -> ComposeResult:
        with VerticalScroll(id="series-body"):
            yield Label("Series", id="series-title")
            yield Label("", id="series-id")
            yield Static("", id="series-fields", markup=True)
            with Horizontal(id="series-actions"):
                yield ActionButton(
                    "Plot series",
                    action_id="plot",
                    shortcut="↵",
                    variant="primary",
                    id="series-plot",
                )
                yield ActionButton(
                    "Collect",
                    action_id="collect",
                    shortcut="space",
                    variant="secondary",
                )
            with Horizontal(id="series-links"):
                yield ActionButton(
                    "Open metric",
                    action_id="open_metric",
                    shortcut="m",
                    variant="ghost",
                    id="series-metric",
                )
                yield ActionButton(
                    "Open entity",
                    action_id="open_entity",
                    shortcut="e",
                    variant="ghost",
                    id="series-entity",
                )
            yield Static("", id="series-status")

    def on_mount(self) -> None:
        super().on_mount()
        ctx = self._input_context
        if self._series_id is None and ctx is not None and ctx.series:
            self._series_id = ctx.series[0]
        if self._series_id:
            # Prefer disk cache on first open; ``r`` forces a network refresh.
            self.call_later(lambda: self.load_series(refresh=False))

    def shell_breadcrumb(self) -> str:
        if self._series:
            return f"Series · {self._series.label or self._series.id}"
        return "Series"

    def bug_report_snapshot(self) -> dict:
        sid = self._series_id
        return {
            "kind": "series",
            "series_id": sid,
            "loaded": self._loaded,
            "label": (
                self._series.label
                if self._series is not None
                else None
            ),
            "api": {"client.get_series": {"id": sid}},
        }

    def bug_report_summary(self) -> str:
        sid = self._series_id or "—"
        return f"[dim]Series[/] [bold]{sid}[/] [dim]→ get_series[/]"

    def footer_action_keys(self) -> list[str]:
        return [
            "[bold #e0e0e0]↵[/] [#888]plot[/]",
            "[bold #e0e0e0]m[/] [#888]metric[/]",
            "[bold #e0e0e0]e[/] [#888]entity[/]",
            "[bold #e0e0e0]space[/] [#888]collect[/]",
        ]

    def action_load(self) -> None:
        if not self._series_id:
            self.notify("No series selected", severity="warning")
            return
        self.load_series(refresh=True)

    @work(exclusive=True, group="series-view")
    async def load_series(self, *, refresh: bool = False) -> None:
        import asyncio

        sid = self._series_id
        if not sid:
            return
        status = self.query_one("#series-status", Static)
        status.update("Loading series…")
        try:
            def _fetch():
                if refresh and hasattr(self.client, "uncached"):
                    with self.client.uncached():
                        return self.client.get_series(sid)
                return self.client.get_series(sid)

            series = await asyncio.to_thread(_fetch)
        except Exception as exc:
            status.update(f"Failed: {exc}")
            self.notify(str(exc), severity="error")
            return
        self._series = series
        self._loaded = True
        if hasattr(self.app, "remember_label"):
            self.app.remember_label(series.id, series.label or series.id)
        self.query_one("#series-title", Label).update(
            series.label or series.id
        )
        self.query_one("#series-id", Label).update(series.id)
        entity_labels = ", ".join(
            e.label or e.id for e in (series.entities or [])
        ) or "—"
        lines = [
            f"[#888]Frequency[/]     {series.frequency or '—'}",
            f"[#888]Units[/]          {series.units or '—'}",
            f"[#888]Source[/]         {series.source or '—'}",
            f"[#888]Seasonal adj.[/]  {series.seasonal_adjustment or '—'}",
            f"[#888]Last updated[/]   {series.last_updated}",
            f"[#888]Metric[/]         {series.metric_id or '—'}",
            f"[#888]Entities[/]       {entity_labels}",
        ]
        self.query_one("#series-fields", Static).update("\n".join(lines))
        status.update("Ready — press Enter to plot")
        self.refresh_footer()

    def action_plot(self) -> None:
        if not self._series_id:
            self.notify("Load a series first", severity="warning")
            return
        open_chart = getattr(self.app, "open_direct_query_chart", None)
        # Prefer series-only draft when host supports ensure_draft.
        ensure = getattr(self.app, "ensure_draft_query", None)
        open_q = getattr(self.app, "open_query_view", None)
        if callable(ensure) and callable(open_q):
            try:
                q = ensure(
                    reset=True,
                    series=[self._series_id],
                    name=self._series.label if self._series else None,
                    tail=20,
                )
                open_q(q.id)
                return
            except Exception as exc:
                self.notify(str(exc), severity="error")
                return
        if callable(open_chart) and self._series and self._series.metric_id:
            ents = [e.id for e in (self._series.entities or [])]
            if ents:
                open_chart([self._series.metric_id], ents)
                return
        self.notify("Could not open chart for this series", severity="warning")

    def action_open_metric(self) -> None:
        if not self._series or not self._series.metric_id:
            self.notify("No metric on this series", severity="warning")
            return
        open_fn = getattr(self.app, "open_resource_view", None)
        if callable(open_fn):
            open_fn("metric", self._series.metric_id)
            return
        self.notify(f"Metric {self._series.metric_id}")

    def action_open_entity(self) -> None:
        if not self._series or not self._series.entities:
            self.notify("No entity on this series", severity="warning")
            return
        eid = self._series.entities[0].id
        open_fn = getattr(self.app, "open_resource_view", None)
        if callable(open_fn):
            open_fn("entity", eid, label=self._series.entities[0].label)
            return
        self.notify(f"Entity {eid}")

    def action_collect(self) -> None:
        if not self._series_id:
            return
        from .base import stage_ids

        labels = {self._series_id: (self._series.label if self._series else self._series_id)}
        added = stage_ids(
            self.workspace,
            self.app,
            series=self._series_id,
            labels=labels,
        )
        if not added:
            self.notify("Already in workspace", severity="warning")
        else:
            self.notify(f"Collected {added[0]}")

    @on(ActionButton.Pressed)
    def _button(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "plot":
            self.action_plot()
        elif event.button_id == "collect":
            self.action_collect()
        elif event.button_id == "open_metric":
            self.action_open_metric()
        elif event.button_id == "open_entity":
            self.action_open_entity()


def create_series_screen(client, workspace, input_context=None, **kwargs):
    series_id = kwargs.pop("series_id", None) or kwargs.pop("series", None)
    if isinstance(series_id, list):
        series_id = series_id[0] if series_id else None
    kwargs.pop("meta", None)
    return SeriesScreen(
        client,
        workspace,
        input_context=input_context,
        series_id=series_id,
    )


SERIES_VIEW = register(
    ActionSpec(
        id="series",
        name="Series",
        description=(
            "Inspect a concrete series (frequency, units, source, metric, "
            "entities). Primary action plots it as a draft query."
        ),
        create_screen=create_series_screen,
        arguments=(),
        bindings=(
            ActionBinding("r", "load", "Load series metadata"),
            ActionBinding("enter / p", "plot", "Plot this series"),
            ActionBinding("m", "open_metric", "Open the parent metric"),
            ActionBinding("e", "open_entity", "Open the parent entity"),
            ActionBinding("space", "collect", "Collect into workspace"),
        ),
        example=':add series --args \'{"series_id":"…"}\'',
    )
)
