"""Entity View — metadata, associated metrics, and relationship edges.

No network until the user presses ``r`` (load). ``/`` searches metrics.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import (
    ResearchContext,
    Workspace,
    focus_replace_context,
)
from ..models import EntityRelationship, Metric
from .base import ActionBinding, ActionSpec, label_for, register, stage_ids
from .screen import BaseScreen

RELATIONS_PAGE = 20
ASSOCIATES_PAGE = 20
SEARCH_DEBOUNCE_S = 0.3

Pane = Literal["metrics", "relations"]


class MetricAssocRow(ListItem):
    def __init__(self, metric: Metric) -> None:
        super().__init__()
        self.metric = metric

    def compose(self) -> ComposeResult:
        name = self.metric.name or self.metric.id
        yield Label(f"{name}  [{self.metric.id}]")


class RelationRow(ListItem):
    def __init__(self, rel: EntityRelationship) -> None:
        super().__init__()
        self.rel = rel

    def compose(self) -> ComposeResult:
        tax = f"  [{self.rel.taxonomy}]" if self.rel.taxonomy else ""
        rel_name = (
            self.rel.relationship.value
            if hasattr(self.rel.relationship, "value")
            else str(self.rel.relationship)
        )
        yield Label(f"{rel_name:<16} → {self.rel.id}{tax}")


class EntityScreen(BaseScreen):
    """Show id/label, associated metrics, and relations for one entity."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("slash", "focus_filter", "Search metrics", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open metric", priority=True),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("tab", "cycle_pane", "Metrics / relations", priority=True),
        Binding(
            "shift+tab",
            "cycle_pane_reverse",
            "Pane reverse",
            show=False,
            priority=True,
        ),
        Binding("space", "collect", "Collect highlighted metric", show=False),
        Binding("a", "collect_self", "Add this entity to workspace", show=False),
    ]

    DEFAULT_CSS = """
    #entity-body {
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
    #entity-fields {
        color: #e0e0e0;
        padding: 1 1 0 1;
        height: auto;
    }
    #entity-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #metrics-header,
    #relations-header {
        background: #111;
        color: #888;
        text-style: bold;
        padding: 0 1;
        height: 1;
        border-top: solid #333;
    }
    #metrics-header.-active,
    #relations-header.-active {
        color: #4ade80;
        background: #163324;
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
    #relations-list {
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
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self._entity_id: Optional[str] = None
        self._label: str = ""
        self._relations: list[EntityRelationship] = []
        self._metrics: list[Metric] = []
        self._pane: Pane = "metrics"
        self._loaded = False
        self._parent_overrides: Optional[ResearchContext] = None
        self._filter_query = ""
        self._search_task: asyncio.Task | None = None
        self._entity_meta_loaded = False

    def _resolved_input_context(self) -> ResearchContext:
        if self._input_context is not None:
            return self._input_context
        app_ctx = getattr(self.app, "input_context", None)
        if isinstance(app_ctx, ResearchContext):
            return app_ctx
        return ResearchContext.from_workspace(self.workspace)

    def _labels(self) -> dict[str, str]:
        try:
            return getattr(self.app, "labels", {}) or {}
        except Exception:
            return {}

    def compose_body(self) -> ComposeResult:
        with Vertical(id="entity-body"):
            with Vertical(classes="pane-container"):
                yield Label("ENTITY", classes="pane-header")
                yield Static("", id="entity-fields", markup=True)
                yield Static(
                    "Idle — press [bold]r[/bold] to load",
                    id="entity-status",
                    markup=True,
                )
                yield Label("METRICS", id="metrics-header")
                yield Input(
                    placeholder="/ search metrics for this entity…",
                    id="assoc-filter",
                )
                yield ListView(id="metrics-list")
                yield Label("RELATIONSHIPS", id="relations-header")
                yield ListView(id="relations-list")

    def on_mount(self) -> None:
        super().on_mount()
        inv = getattr(self.app, "investigation", None)
        active = getattr(self.app, "active", None)
        if inv is not None and active is not None:
            try:
                self._parent_overrides = inv.get_action(active.action).context
            except Exception:
                self._parent_overrides = None
        ctx = self._resolved_input_context()
        if not ctx.entity:
            self.query_one("#entity-fields", Static).update(
                "No entity in Input Context.\n"
                "Drill into an entity from a table, or stage one via :search."
            )
            self.query_one("#entity-status", Static).update("")
            return
        self._entity_id = ctx.entity[0]
        self._label = label_for(self._labels(), self._entity_id)
        note = ""
        if len(ctx.entity) > 1:
            note = f"\n(+{len(ctx.entity) - 1} more entities in Input Context; showing first)"
        self._render_fields(note=note)
        self._focus_pane("metrics")

    def _render_fields(self, *, note: str = "") -> None:
        eid = self._entity_id or "—"
        text = (
            f"[bold]id[/bold]     {eid}\n"
            f"[bold]label[/bold]  {self._label or '—'}"
            f"{note}"
        )
        self.query_one("#entity-fields", Static).update(text)

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold]r[/bold] load"]
        if self._loaded:
            keys.append("[bold]/[/bold] search")
            keys.append("[bold]tab[/bold] metrics/rels")
            keys.append("[bold]j[/bold]/[bold]k[/bold] move")
            if self._pane == "metrics":
                keys.append("[bold]enter[/bold] metric")
                keys.append("[bold]space[/bold] collect metric")
        keys.append("[bold]a[/bold] add entity")
        return keys

    def _active_list(self) -> ListView:
        if self._pane == "metrics":
            return self.query_one("#metrics-list", ListView)
        return self.query_one("#relations-list", ListView)

    def _focus_pane(self, pane: Pane) -> None:
        self._pane = pane
        self.query_one("#metrics-header", Label).set_class(pane == "metrics", "-active")
        self.query_one("#relations-header", Label).set_class(
            pane == "relations", "-active"
        )
        self._active_list().focus()
        self.refresh_footer()

    def action_load(self) -> None:
        if not self._entity_id:
            self.notify("No entity in Input Context", severity="warning")
            return
        self._filter_query = ""
        self.query_one("#assoc-filter", Input).value = ""
        self.load_entity(reset_meta=True)

    def action_focus_filter(self) -> None:
        if not self._loaded:
            self.notify("Press r to load first", severity="warning")
            return
        self._focus_pane("metrics")
        self.query_one("#assoc-filter", Input).focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#assoc-filter", Input)
        if self.focused is filt:
            self.query_one("#metrics-list", ListView).focus()
            self._pane = "metrics"
            self.refresh_footer()

    def action_cycle_pane(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        self._focus_pane("relations" if self._pane == "metrics" else "metrics")

    def action_cycle_pane_reverse(self) -> None:
        self.action_cycle_pane()

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self._active_list()
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self._active_list()
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    def _highlighted_metric(self) -> Optional[Metric]:
        child = self.query_one("#metrics-list", ListView).highlighted_child
        if isinstance(child, MetricAssocRow):
            return child.metric
        return None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "collect" and isinstance(self.focused, Input):
            return False
        return True

    def action_collect_self(self) -> None:
        """Stage the focused entity (this page), not a list associate."""
        if not self._entity_id:
            self.notify("No entity in Input Context", severity="warning")
            return
        added = stage_ids(
            self.workspace,
            self.app,
            entity=self._entity_id,
            labels={self._entity_id: self._label or self._entity_id},
        )
        if not added:
            self.notify(f"{self._entity_id} already in workspace", severity="warning")
            return
        self.notify(f"Added entity {self._label or self._entity_id}")

    def action_collect(self) -> None:
        """Stage the highlighted metric associate (metrics pane)."""
        if isinstance(self.focused, Input):
            return
        if self._pane != "metrics":
            self.notify(
                "Switch to metrics (Tab), or press a to add this entity",
                severity="warning",
            )
            return
        metric = self._highlighted_metric() if self._loaded else None
        if metric is None:
            self.notify(
                "Highlight a metric, or press a to add this entity",
                severity="warning",
            )
            return
        added = stage_ids(
            self.workspace,
            self.app,
            metric=metric.id,
            labels={metric.id: metric.name or metric.id},
        )
        if not added:
            self.notify(f"{metric.id} already in workspace", severity="warning")
            return
        self.notify(f"Collected metric {metric.name or metric.id}")

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        if not self._loaded:
            self.notify("Press r to load metrics first", severity="warning")
            return
        if self._pane != "metrics":
            self.notify(
                "Switch to the metrics list (Tab) to open a metric",
                severity="warning",
            )
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
        if isinstance(event.item, MetricAssocRow):
            self._pane = "metrics"
            self.action_confirm()

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
            self.load_entity(reset_meta=False)
        except asyncio.CancelledError:
            pass

    def _show_metrics(self, metrics: list[Metric]) -> None:
        self._metrics = list(metrics)
        metrics_list = self.query_one("#metrics-list", ListView)
        metrics_list.clear()
        for metric in self._metrics:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(metric.id, metric.name or metric.id)
            metrics_list.append(MetricAssocRow(metric))

    def _show_relations(self, relations: list[EntityRelationship]) -> None:
        self._relations = list(relations)
        rel_list = self.query_one("#relations-list", ListView)
        rel_list.clear()
        for rel in self._relations:
            rel_list.append(RelationRow(rel))

    @work(exclusive=True, group="entity-view")
    async def load_entity(self, *, reset_meta: bool = True) -> None:
        entity_id = self._entity_id
        if not entity_id:
            return
        status = self.query_one("#entity-status", Static)
        status.update("Loading…")
        query = self._filter_query.strip() or None
        try:
            if reset_meta or not self._entity_meta_loaded:
                entity, relations, metrics = await asyncio.gather(
                    asyncio.to_thread(self.client.get_entity, entity_id),
                    asyncio.to_thread(
                        self.client.get_entity_relations,
                        entity_id,
                        RELATIONS_PAGE,
                        0,
                    ),
                    asyncio.to_thread(
                        self.client.search_metrics,
                        query,
                        entity=entity_id,
                        limit=ASSOCIATES_PAGE,
                        offset=0,
                    ),
                )
                if entity_id != self._entity_id:
                    return
                self._label = entity.label or entity.id
                if hasattr(self.app, "remember_label"):
                    self.app.remember_label(entity_id, self._label)
                note = ""
                ctx = self._resolved_input_context()
                if len(ctx.entity) > 1:
                    note = (
                        f"\n(+{len(ctx.entity) - 1} more entities in Input Context; "
                        "showing first)"
                    )
                self._render_fields(note=note)
                self._show_relations(list(relations))
                self._entity_meta_loaded = True
            else:
                metrics = await asyncio.to_thread(
                    self.client.search_metrics,
                    query,
                    entity=entity_id,
                    limit=ASSOCIATES_PAGE,
                    offset=0,
                )
                if entity_id != self._entity_id:
                    return
        except Exception as exc:
            status.update(f"Could not load entity: {exc}")
            return

        self._loaded = True
        self._show_metrics(list(metrics))
        m_extra = "+" if len(self._metrics) >= ASSOCIATES_PAGE else ""
        r_extra = "+" if len(self._relations) >= RELATIONS_PAGE else ""
        qbit = f" · “{query}”" if query else ""
        status.update(
            f"{len(self._metrics)}{m_extra} metric(s){qbit} · "
            f"{len(self._relations)}{r_extra} relationship(s)"
        )
        self.refresh_footer()


def create_entity_screen(client, workspace, input_context=None, **kwargs):
    return EntityScreen(client, workspace, input_context=input_context)


ENTITY_VIEW = register(
    ActionSpec(
        id="entity",
        name="Entity",
        description=(
            "Inspect a focused entity (id, label, associated metrics, "
            "relationships). Idle until load (r); / searches metrics."
        ),
        create_screen=create_entity_screen,
        arguments=(),
        bindings=(
            ActionBinding("r", "load", "Load entity details, metrics, and relations"),
            ActionBinding("/", "focus_filter", "Search metrics for this entity"),
            ActionBinding("tab", "cycle_pane", "Switch metrics / relationships lists"),
            ActionBinding("j / k", "cursor", "Move in the active list"),
            ActionBinding(
                "enter",
                "confirm",
                "Open metric view for highlighted metric (metrics pane)",
            ),
            ActionBinding(
                "a",
                "collect_self",
                "Add this entity to the workspace",
            ),
            ActionBinding(
                "space",
                "collect",
                "Collect highlighted metric into the workspace",
            ),
        ),
        example="jst action add entity",
    )
)
