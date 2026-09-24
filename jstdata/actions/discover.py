"""Discover action — metric candidates for workspace entities, with a preview chart."""

from __future__ import annotations

import asyncio
from typing import Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..models import Metric, TimeSeries
from ..investigation.model import Workspace
from .base import ActionArgument, ActionBinding, ActionSpec, label_for, register
from .chart import LABEL_WIDTH, pick_frequency, pick_series_per_entity, render_preview

PAGE_SIZE = 20
PREVIEW_TAIL = 20
MODES = ("union", "intersect")


class EntityRow(ListItem):
    can_focus = False

    def __init__(self, entity_id: str, caption: str) -> None:
        super().__init__()
        self.entity_id = entity_id
        self.caption = caption

    def compose(self) -> ComposeResult:
        yield Label(self.caption)


class MetricRow(ListItem):
    def __init__(self, metric: Metric, staged: bool = False) -> None:
        super().__init__()
        self.metric = metric
        self.staged = staged

    def compose(self) -> ComposeResult:
        mark = "● " if self.staged else "  "
        yield Label(f"{mark}{self.metric.name}  [{self.metric.id}]")


class DiscoverScreen(Screen):
    """Entities roster, candidate metrics, and a recent-observation bar preview."""

    BINDINGS = [
        Binding("slash", "focus_filter", "Filter metrics", key_display="/"),
        Binding("escape", "leave_filter", "Leave filter", show=False),
        Binding("tab", "cycle_panes", "Switch list / preview", priority=True),
        Binding(
            "shift+tab",
            "cycle_panes",
            "Switch panes reverse",
            show=False,
            priority=True,
        ),
        Binding("enter", "preview_metric", "Preview metric", priority=True),
        Binding("space", "add_metric", "Add metric to workspace", priority=True),
        Binding("j", "cursor_down", "Move highlight down", show=False),
        Binding("k", "cursor_up", "Move highlight up", show=False),
    ]

    DEFAULT_CSS = """
    #discover-body { height: 1fr; }

    .pane-container {
        border: solid #222;
        background: #0f0f0f;
    }
    #top-row { height: 55%; }
    #entities-pane { width: 32%; }
    #metrics-pane { width: 1fr; }
    #preview-pane { height: 1fr; border-top: solid #333; }

    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }

    #entities-list, #metrics-list {
        height: 1fr;
        background: #0f0f0f;
    }

    ListView > ListItem,
    EntityRow,
    MetricRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }

    MetricRow:focus,
    MetricRow.-highlighted {
        background: #1a3a1a;
        color: #4ade80;
    }

    #empty-prompt {
        height: 1fr;
        padding: 2 2;
        color: #4ade80;
        text-style: bold;
        background: #111111;
    }

    #metrics-filter {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 1;
    }

    #metrics-status {
        height: 1;
        color: #888;
        padding: 0 1;
    }

    #preview-scroll { height: 1fr; background: #0f0f0f; }
    #preview-chart {
        width: 100%;
        height: auto;
        padding: 0 1;
        color: #e0e0e0;
    }

    #cmd-bar {
        height: 3;
        background: #0f0f0f;
        border-top: solid #4ade80;
        align: left middle;
        padding: 0 1;
    }
    #help-hint { color: #888; }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        mode: str = "union",
        workspace_metrics: bool = True,
    ):
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.mode = mode if mode in MODES else "union"
        self.workspace_metrics = bool(workspace_metrics)
        self.filter_query = ""
        self.page_offset = 0
        self.has_more = False
        self.loading_more = False
        self.search_task: asyncio.Task[None] | None = None
        self._entity_snapshot: tuple[str, ...] = ()
        self._metric_snapshot: tuple[str, ...] = ()
        self._metrics: list[Metric] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="discover-body"):
            with Horizontal(id="top-row"):
                with Vertical(classes="pane-container", id="entities-pane"):
                    yield Label("ENTITIES // WORKSPACE", classes="pane-header")
                    yield ListView(id="entities-list")
                    yield Static(
                        "No entities in workspace.\n\n"
                        "Press [bold]f[/bold] to find entities and add them.\n"
                        "Metrics in this action are generated from that set.",
                        id="empty-prompt",
                        markup=True,
                    )
                with Vertical(classes="pane-container", id="metrics-pane"):
                    yield Label(
                        "METRICS // CANDIDATES",
                        classes="pane-header",
                        id="metrics-header",
                    )
                    yield Input(
                        placeholder="/ filter metrics in this set…",
                        id="metrics-filter",
                    )
                    yield ListView(id="metrics-list")
                    yield Label("", id="metrics-status")
            with Vertical(classes="pane-container", id="preview-pane"):
                yield Label(
                    "PREVIEW // LAST 20 OBS",
                    classes="pane-header",
                    id="preview-header",
                )
                with ScrollableContainer(id="preview-scroll"):
                    yield Static(
                        "Highlight a metric and press Enter to preview.",
                        id="preview-chart",
                    )
        with Horizontal(id="cmd-bar"):
            yield Label("", id="help-hint")

    def on_mount(self) -> None:
        self.query_one("#entities-list", ListView).can_focus = False
        self._refresh_help_hint()
        self._sync_entities(force=True)
        self.query_one("#metrics-list", ListView).focus()

    def on_screen_resume(self) -> None:
        self._sync_entities()

    def _labels(self) -> dict[str, str]:
        return getattr(self.app, "labels", {})

    def _refresh_help_hint(self) -> None:
        n = getattr(self.app, "action_count", 1)
        i = getattr(self.app, "action_index", 0) + 1
        scope = "workspace metrics" if self._using_workspace_metrics() else f"mode {self.mode}"
        parts = [
            f"discover {i}/{n}",
            scope,
            "[bold]/[/bold] filter",
            "[bold]enter[/bold] preview",
            "[bold]space[/bold] add",
            "[bold]tab[/bold] panes",
            "[bold]j/k[/bold] move",
            "[bold]:[/bold] workspace",
            "[bold]f[/bold] find",
            "[bold]e[/bold] export",
            "[bold]n[/bold]/[bold]p[/bold]",
            "[bold]q[/bold] quit",
            "[bold]?[/bold]",
        ]
        self.query_one("#help-hint").update(" // ".join(parts))

    def _using_workspace_metrics(self) -> bool:
        """True when the metrics pane is limited to non-empty workspace.metric."""
        return bool(self.workspace_metrics and self.workspace.metric)

    def _sync_entities(self, force: bool = False) -> None:
        entity_snap = tuple(self.workspace.entity)
        metric_snap = tuple(self.workspace.metric)
        if (
            not force
            and entity_snap == self._entity_snapshot
            and metric_snap == self._metric_snapshot
        ):
            return
        self._entity_snapshot = entity_snap
        self._metric_snapshot = metric_snap
        empty = self.query_one("#empty-prompt")
        roster = self.query_one("#entities-list", ListView)
        roster.clear()
        if not entity_snap:
            empty.display = True
            roster.display = False
            self.query_one("#metrics-list", ListView).clear()
            self._metrics = []
            self.query_one("#metrics-status").update(
                "Add entities to generate metrics."
            )
            self._refresh_help_hint()
            return
        empty.display = False
        roster.display = True
        labels = self._labels()
        for eid in entity_snap:
            roster.append(EntityRow(eid, label_for(labels, eid)))
        self.page_offset = 0
        self._metrics = []
        self.query_one("#metrics-list", ListView).clear()
        self._refresh_help_hint()
        self.load_metrics(reset=True)

    async def _fetch_workspace_metrics(self) -> list[Metric]:
        """Resolve workspace metric ids to Metric rows, optionally name-filtered."""
        ids = list(self.workspace.metric)
        labels = self._labels()
        missing = [mid for mid in ids if mid not in labels]
        if missing:
            try:
                resources = await asyncio.to_thread(self.client.get_resources, missing)
            except Exception:
                resources = []
            remember = getattr(self.app, "remember_label", None)
            for resource in resources:
                name = resource.label or resource.id
                labels[resource.id] = name
                if callable(remember):
                    remember(resource.id, name)
        q = self.filter_query.strip().lower()
        metrics: list[Metric] = []
        for mid in ids:
            name = label_for(labels, mid)
            if q and q not in mid.lower() and q not in name.lower():
                continue
            metrics.append(Metric(id=mid, name=name))
        return metrics

    @work(exclusive=True, group="metrics")
    async def load_metrics(self, reset: bool = False) -> None:
        if not self.workspace.entity:
            self.loading_more = False
            return
        if reset:
            self.page_offset = 0
            self.has_more = False
        status = self.query_one("#metrics-status")
        status.update("Loading metrics…")
        query = self.filter_query.strip() or None
        using_workspace = self._using_workspace_metrics()
        try:
            if using_workspace:
                metrics = await self._fetch_workspace_metrics()
            else:
                metrics = await asyncio.to_thread(
                    self.client.search_metrics,
                    query,
                    entity=list(self.workspace.entity),
                    mode=self.mode,
                    limit=PAGE_SIZE,
                    offset=self.page_offset,
                )
        except Exception as e:
            status.update(f"Error: {e}")
            self.loading_more = False
            return
        if reset:
            self._metrics = []
            self.query_one("#metrics-list", ListView).clear()
        self._metrics.extend(metrics)
        self.has_more = (not using_workspace) and len(metrics) == PAGE_SIZE
        list_view = self.query_one("#metrics-list", ListView)
        staged = set(self.workspace.metric)
        for metric in metrics:
            list_view.append(MetricRow(metric, staged=metric.id in staged))
        shown = len(self._metrics)
        extra = "+" if self.has_more else ""
        if using_workspace:
            qlabel = f"“{query}”" if query else "workspace"
            status.update(f"{shown} · {qlabel}")
            self.query_one("#metrics-header").update("METRICS // WORKSPACE")
        else:
            qlabel = f"“{query}”" if query else "all"
            status.update(f"{shown}{extra} · {qlabel} · {self.mode}")
            self.query_one("#metrics-header").update(f"METRICS // {self.mode.upper()}")
        self.loading_more = False

    def action_focus_filter(self) -> None:
        filt = self.query_one("#metrics-filter", Input)
        filt.focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#metrics-filter", Input)
        if self.focused is filt:
            self.query_one("#metrics-list", ListView).focus()

    def action_cycle_panes(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#metrics-list", ListView).focus()
            return
        if self.focused is self.query_one("#metrics-list"):
            self.query_one("#preview-scroll").focus()
        else:
            self.query_one("#metrics-list", ListView).focus()

    def _preview_is_focused(self) -> bool:
        node = self.focused
        while node is not None:
            if getattr(node, "id", None) == "preview-scroll":
                return True
            node = getattr(node, "parent", None)
        return False

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._preview_is_focused():
            self.query_one("#preview-scroll", ScrollableContainer).scroll_relative(
                y=1, animate=False
            )
            return
        self.query_one("#metrics-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._preview_is_focused():
            self.query_one("#preview-scroll", ScrollableContainer).scroll_relative(
                y=-1, animate=False
            )
            return
        self.query_one("#metrics-list", ListView).action_cursor_up()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "add_metric" and isinstance(self.focused, Input):
            return False
        return True

    def _highlighted_metric(self) -> Optional[Metric]:
        child = self.query_one("#metrics-list", ListView).highlighted_child
        if isinstance(child, MetricRow):
            return child.metric
        return None

    def action_preview_metric(self) -> None:
        if isinstance(self.focused, Input):
            return
        metric = self._highlighted_metric()
        if metric is not None:
            self.run_preview(metric)

    def action_add_metric(self) -> None:
        if isinstance(self.focused, Input):
            return
        list_view = self.query_one("#metrics-list", ListView)
        if list_view.index is None and self._metrics:
            list_view.index = 0
        metric = self._highlighted_metric()
        if metric is None:
            self.notify("Highlight a metric first", severity="warning")
            return
        if not self.workspace.add_metric(metric.id):
            self.notify(f"{metric.id} is already in workspace", severity="warning")
            return
        if hasattr(self.app, "remember_label"):
            self.app.remember_label(metric.id, metric.name or metric.id)
        self.notify(f"Added {metric.id} to workspace")
        child = self.query_one("#metrics-list", ListView).highlighted_child
        if isinstance(child, MetricRow):
            child.staged = True
            try:
                child.query_one(Label).update(f"● {metric.name}  [{metric.id}]")
            except Exception:
                pass

    @on(Input.Changed, "#metrics-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        if self.search_task:
            self.search_task.cancel()
        self.search_task = asyncio.create_task(self._debounced_filter(event.value))

    async def _debounced_filter(self, value: str) -> None:
        try:
            await asyncio.sleep(0.3)
            self.filter_query = value
            self.load_metrics(reset=True)
        except asyncio.CancelledError:
            pass

    @on(Input.Submitted, "#metrics-filter")
    def on_filter_submit(self) -> None:
        self.query_one("#metrics-list", ListView).focus()

    @on(ListView.Highlighted, "#metrics-list")
    def on_metric_highlighted(self, event: ListView.Highlighted) -> None:
        if self.loading_more or not self.has_more:
            return
        list_view = self.query_one("#metrics-list", ListView)
        if event.item is None:
            return
        index = list_view.index
        if index is None or index < len(self._metrics) - 1:
            return
        self.loading_more = True
        self.page_offset = len(self._metrics)
        self.load_metrics(reset=False)

    @work(exclusive=True, group="preview")
    async def run_preview(self, metric: Metric) -> None:
        chart = self.query_one("#preview-chart", Static)
        header = self.query_one("#preview-header")
        if not self.workspace.entity:
            chart.update("Add entities before previewing.")
            return
        header.update(f"PREVIEW // {metric.name}")
        chart.update("Loading observations…")
        try:
            series_list: list[TimeSeries] = await asyncio.to_thread(
                self.client.query,
                metric=metric.id,
                entity=list(self.workspace.entity),
                tail=PREVIEW_TAIL,
            )
        except Exception as e:
            chart.update(f"Preview failed: {e}")
            return
        frequency = pick_frequency(series_list)
        if not frequency:
            chart.update("No observations in the preview window.")
            return
        picked = pick_series_per_entity(series_list, self.workspace.entity, frequency)
        units = next(
            (ts.series.units for ts in picked.values() if ts is not None),
            "",
        )
        width = max(24, (self.size.width or 80) - LABEL_WIDTH - 2)
        text = render_preview(
            metric.name,
            frequency,
            units,
            {eid: label_for(self._labels(), eid) for eid in self.workspace.entity},
            picked,
            bar_width=width,
        )
        chart.update(text)


def create_discover_screen(
    client, workspace, mode="union", workspace_metrics=True, **kwargs
):
    return DiscoverScreen(
        client, workspace, mode=mode, workspace_metrics=workspace_metrics
    )


DISCOVER = register(
    ActionSpec(
        id="discover",
        name="Discover",
        description=(
            "Find metrics related to workspace entities, preview recent coverage, "
            "and stage promising metrics."
        ),
        create_screen=create_discover_screen,
        arguments=(
            ActionArgument(
                name="mode",
                type="string",
                description="union (any entity) or intersect (every entity)",
                default="union",
                choices=MODES,
            ),
            ActionArgument(
                name="workspace_metrics",
                type="boolean",
                description=(
                    "When true (default), show only workspace metrics if any "
                    "are present; with an empty metric list, searches all "
                    "metrics for the workspace entities. Set false to always "
                    "search the full candidate set."
                ),
                default=True,
            ),
        ),
        bindings=(
            ActionBinding("/", "focus_filter", "Filter the generated metric list"),
            ActionBinding(
                "enter",
                "preview_metric",
                "Preview the latest observations for the highlighted metric",
            ),
            ActionBinding(
                "space", "add_metric", "Add highlighted metric to the workspace"
            ),
            ActionBinding(
                "tab", "cycle_panes", "Switch focus between metrics and preview"
            ),
            ActionBinding("j / k", "cursor", "Move metric highlight or scroll preview"),
            ActionBinding("escape", "leave_filter", "Leave the metric filter"),
        ),
        example="jst action add discover --args '{\"mode\":\"union\"}'",
    )
)
