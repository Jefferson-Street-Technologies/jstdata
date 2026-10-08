"""Metrics View — metrics associated with a set of entities (union/intersect).

Entity set comes from Input Context (overrides), else the global workspace
entities. ``mode`` is seeded from YAML ``args`` (union|intersect). Idle until
``r``; ``/`` searches.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import (
    ResearchContext,
    Workspace,
    focus_replace_context,
)
from ..models import Metric
from .base import ActionArgument, ActionBinding, ActionSpec, label_for, register
from .pagination import page_fetch_limit, take_page
from .screen import BaseScreen

PAGE_SIZE = 20
SEARCH_DEBOUNCE_S = 0.3
MODES = ("union", "intersect")


class MetricRow(ListItem):
    def __init__(self, metric: Metric) -> None:
        super().__init__()
        self.metric = metric

    def compose(self) -> ComposeResult:
        name = self.metric.name or self.metric.id
        yield Label(f"{name}  [{self.metric.id}]")


class MetricsScreen(BaseScreen):
    """List metrics for the Input Context entity set under union/intersect."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("slash", "focus_filter", "Search metrics", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open metric", priority=True),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("m", "toggle_mode", "Toggle union/intersect", key_display="m"),
    ]

    DEFAULT_CSS = """
    #metrics-body {
        height: 1fr;
    }
    .pane-container {
        border: solid #222;
        background: #0f0f0f;
        height: 1fr;
    }
    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    #mode-bar {
        height: 1;
        background: #111;
        padding: 0 1;
    }
    #mode-chip {
        width: auto;
        padding: 0 1;
        text-style: bold;
    }
    #mode-chip.-union {
        background: #1e3a5f;
        color: #93c5fd;
    }
    #mode-chip.-intersect {
        background: #3b1f4b;
        color: #e9d5ff;
    }
    #mode-hint {
        color: #666;
        width: 1fr;
        padding: 0 1;
    }
    #scope-line {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #metrics-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #list-header {
        background: #111;
        color: #888;
        text-style: bold;
        padding: 0 1;
        height: 1;
        border-top: solid #333;
    }
    #assoc-filter {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 1;
    }
    #metrics-list {
        height: 1fr;
        background: #0f0f0f;
    }
    ListView > ListItem,
    ListView > ListItem:hover {
        background: transparent;
        color: #e0e0e0;
    }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        input_context: Optional[ResearchContext] = None,
        mode: str = "union",
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self.mode = mode if mode in MODES else "union"
        self._metrics: list[Metric] = []
        self._loaded = False
        self._parent_overrides: Optional[ResearchContext] = None
        self._filter_query = ""
        self._search_task: asyncio.Task | None = None
        self.page_offset = 0
        self.has_more = False
        self.loading_more = False

    def _resolved_input_context(self) -> ResearchContext:
        if self._input_context is not None:
            return self._input_context
        app_ctx = getattr(self.app, "input_context", None)
        if isinstance(app_ctx, ResearchContext):
            return app_ctx
        return ResearchContext.from_workspace(self.workspace)

    def _entity_ids(self) -> list[str]:
        return list(self._resolved_input_context().entity)

    def _labels(self) -> dict[str, str]:
        try:
            return getattr(self.app, "labels", {}) or {}
        except Exception:
            return {}

    def compose_body(self) -> ComposeResult:
        with Vertical(id="metrics-body"):
            with Vertical(classes="pane-container"):
                yield Label("METRICS // ENTITIES", classes="pane-header", id="title")
                with Horizontal(id="mode-bar"):
                    yield Label("", id="mode-chip")
                    yield Label("m toggles mode", id="mode-hint")
                yield Static("", id="scope-line")
                yield Static(
                    "Idle — press [bold]r[/bold] to load",
                    id="metrics-status",
                    markup=True,
                )
                yield Label("METRICS", id="list-header")
                yield Input(
                    placeholder="/ search metrics for these entities…",
                    id="assoc-filter",
                )
                yield ListView(id="metrics-list")

    def on_mount(self) -> None:
        super().on_mount()
        inv = getattr(self.app, "investigation", None)
        active = getattr(self.app, "active", None)
        if inv is not None and active is not None:
            try:
                action = inv.get_action(active.action)
                self._parent_overrides = action.context
                # Prefer persisted args.mode if present on the node.
                raw = (action.args or {}).get("mode")
                if raw in MODES:
                    self.mode = raw
            except Exception:
                self._parent_overrides = None
        self._refresh_mode_chrome()
        self._refresh_scope_line()
        self.query_one("#metrics-list", ListView).focus()

    def _refresh_mode_chrome(self) -> None:
        chip = self.query_one("#mode-chip", Label)
        chip.set_class(self.mode == "union", "-union")
        chip.set_class(self.mode == "intersect", "-intersect")
        if self.mode == "union":
            chip.update("UNION")
        else:
            chip.update("INTERSECT")
        self.query_one("#title", Label).update(
            f"METRICS // ENTITIES // {self.mode.upper()}"
        )

    def _refresh_scope_line(self) -> None:
        ids = self._entity_ids()
        labels = self._labels()
        if not ids:
            self.query_one("#scope-line", Static).update(
                "No entities in Input Context (or global workspace)"
            )
            return
        bits = []
        for eid in ids[:6]:
            bits.append(labels.get(eid) or eid)
        extra = f" +{len(ids) - 6}" if len(ids) > 6 else ""
        self.query_one("#scope-line", Static).update(
            f"{len(ids)} entit{'y' if len(ids) == 1 else 'ies'}: "
            + ", ".join(bits)
            + extra
        )

    def footer_action_keys(self) -> list[str]:
        keys = [
            "[bold]r[/bold] load",
            f"[bold]m[/bold] {self.mode}",
        ]
        if self._loaded:
            keys.append("[bold]/[/bold] search")
            keys.append("[bold]j[/bold]/[bold]k[/bold] move")
            keys.append("[bold]enter[/bold] metric")
        return keys

    def action_toggle_mode(self) -> None:
        """Flip union/intersect and persist into the action node's YAML args."""
        self.mode = "intersect" if self.mode == "union" else "union"
        self._persist_mode_arg()
        self._refresh_mode_chrome()
        self.refresh_footer()
        if self._loaded:
            self.load_metrics(reset=True)

    def _persist_mode_arg(self) -> None:
        inv = getattr(self.app, "investigation", None)
        active = getattr(self.app, "active", None)
        if inv is None or active is None:
            return
        try:
            inv.get_action(active.action).args["mode"] = self.mode
        except Exception:
            return
        from ..investigation.api import persist_investigation

        app = self.app
        was_suppressed = getattr(app, "_suppress_poll", False)
        try:
            app._suppress_poll = True
            persist_investigation(inv, app_dir=getattr(app, "app_dir", None))
            inv_file = getattr(app, "_inv_file", None)
            if callable(inv_file):
                try:
                    app._inv_mtime = inv_file().stat().st_mtime
                except OSError:
                    pass
        except Exception:
            pass
        finally:
            try:
                app._suppress_poll = was_suppressed
            except Exception:
                pass

    def action_load(self) -> None:
        if not self._entity_ids():
            self.notify(
                "No entities to query — add some via :search or focus overrides",
                severity="warning",
            )
            return
        self._filter_query = ""
        self.query_one("#assoc-filter", Input).value = ""
        self.load_metrics(reset=True)

    def action_focus_filter(self) -> None:
        if not self._loaded:
            self.notify("Press r to load first", severity="warning")
            return
        self.query_one("#assoc-filter", Input).focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#assoc-filter", Input)
        if self.focused is filt:
            self.query_one("#metrics-list", ListView).focus()

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self.query_one("#metrics-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self.query_one("#metrics-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    def _highlighted_metric(self) -> Optional[Metric]:
        child = self.query_one("#metrics-list", ListView).highlighted_child
        if isinstance(child, MetricRow):
            return child.metric
        return None

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        if not self._loaded:
            self.notify("Press r to load metrics first", severity="warning")
            return
        metric = self._highlighted_metric()
        if metric is None:
            self.notify("No metric highlighted", severity="warning")
            return
        open_fn = getattr(self.app, "open_child_view", None)
        if open_fn is None:
            self.notify("Host cannot open child views", severity="error")
            return
        open_fn(
            "metric",
            context=focus_replace_context(
                self._parent_overrides, metric=[metric.id]
            ),
            description=f"Metric {metric.name or metric.id}",
        )

    @on(ListView.Selected, "#metrics-list")
    def on_metric_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MetricRow):
            self.action_confirm()

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

    @on(Input.Changed, "#assoc-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        if not self._loaded:
            return
        self._filter_query = event.value
        if self._search_task:
            self._search_task.cancel()
        self._search_task = asyncio.create_task(self._debounced_search())

    @on(Input.Submitted, "#assoc-filter")
    def on_filter_submit(self) -> None:
        self.action_leave_filter()

    async def _debounced_search(self) -> None:
        try:
            await asyncio.sleep(SEARCH_DEBOUNCE_S)
            self.load_metrics(reset=True)
        except asyncio.CancelledError:
            pass

    def _append_metrics(self, metrics: list[Metric]) -> None:
        list_view = self.query_one("#metrics-list", ListView)
        for metric in metrics:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(metric.id, metric.name or metric.id)
            list_view.append(MetricRow(metric))

    def _update_status(self, entity_ids: list[str], query: str | None) -> None:
        status = self.query_one("#metrics-status", Static)
        extra = "+" if self.has_more else ""
        qbit = f" · “{query}”" if query else ""
        status.update(
            f"{len(self._metrics)}{extra} metric(s) · {self.mode} · "
            f"{len(entity_ids)} entit{'y' if len(entity_ids) == 1 else 'ies'}{qbit}"
        )

    @work(exclusive=True, group="metrics-view")
    async def load_metrics(self, reset: bool = False) -> None:
        entity_ids = self._entity_ids()
        if not entity_ids:
            self.loading_more = False
            self.query_one("#metrics-status", Static).update(
                "No entities in Input Context"
            )
            return
        if reset:
            self.page_offset = 0
            self.has_more = False
            self.loading_more = False
        status = self.query_one("#metrics-status", Static)
        status.update("Loading…" if reset or not self._loaded else "Loading more…")
        self._refresh_scope_line()
        query = self._filter_query.strip() or None
        try:
            raw = await asyncio.to_thread(
                self.client.search_metrics,
                query,
                entity=entity_ids,
                mode=self.mode,
                limit=page_fetch_limit(PAGE_SIZE),
                offset=self.page_offset,
            )
            metrics, has_more = take_page(raw, PAGE_SIZE)
        except Exception as exc:
            status.update(f"Could not load metrics: {exc}")
            self.loading_more = False
            return
        if reset:
            self._metrics = []
            self.query_one("#metrics-list", ListView).clear()
        self._metrics.extend(metrics)
        self.has_more = has_more
        self._loaded = True
        self.loading_more = False
        self._append_metrics(metrics)
        self._update_status(entity_ids, query)
        self.refresh_footer()


def create_metrics_screen(
    client, workspace, input_context=None, mode="union", **kwargs
):
    return MetricsScreen(
        client,
        workspace,
        input_context=input_context,
        mode=mode,
    )


METRICS_VIEW = register(
    ActionSpec(
        id="metrics",
        name="Metrics",
        description=(
            "List metrics associated with the Input Context entity set "
            "(union or intersect). Idle until load (r)."
        ),
        create_screen=create_metrics_screen,
        arguments=(
            ActionArgument(
                name="mode",
                type="string",
                description="union (any entity) or intersect (every entity)",
                default="union",
                choices=MODES,
            ),
        ),
        bindings=(
            ActionBinding("r", "load", "Load metrics for the entity set"),
            ActionBinding("m", "toggle_mode", "Toggle union / intersect"),
            ActionBinding("/", "focus_filter", "Search within associated metrics"),
            ActionBinding("j / k", "cursor", "Move in metrics list (loads more at end)"),
            ActionBinding("enter", "confirm", "Open metric view for highlighted row"),
        ),
        example=':add metrics --args \'{"mode":"intersect"}\'',
    )
)
