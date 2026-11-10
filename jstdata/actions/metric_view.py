"""Metric View — metadata plus entities that carry the metric.

No network until the user presses ``r`` (load). ``/`` searches entities.
j/k on the last row fetches the next page.
"""

from __future__ import annotations

import asyncio
from typing import Optional

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
from ..models import Entity, Metric, MetricTaxonomy
from .base import ActionBinding, ActionSpec, label_for, register, stage_ids
from .pagination import page_fetch_limit, take_page
from .screen import BaseScreen

ASSOCIATES_PAGE = 20
TAXONOMIES_PAGE = 12
SEARCH_DEBOUNCE_S = 0.3


class EntityAssocRow(ListItem):
    def __init__(self, entity: Entity) -> None:
        super().__init__()
        self.entity = entity

    def compose(self) -> ComposeResult:
        yield Label(f"{self.entity.label or self.entity.id}  [{self.entity.id}]")


class MetricScreen(BaseScreen):
    """Show id/label for the Input Context metric focus, plus covered entities."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("slash", "focus_filter", "Search entities", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open entity", priority=True),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("space", "collect", "Collect into workspace", show=False),
    ]

    DEFAULT_CSS = """
    #metric-body {
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
    #metric-fields {
        color: #e0e0e0;
        padding: 1 1 0 1;
        height: auto;
        max-height: 14;
    }
    #metric-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #entities-header {
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
    #entities-list {
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
        self._metric_id: Optional[str] = None
        self._label: str = ""
        self._entities: list[Entity] = []
        self._loaded = False
        self._parent_overrides: Optional[ResearchContext] = None
        self._filter_query = ""
        self._search_task: asyncio.Task | None = None
        self._metric_meta_loaded = False
        self._taxonomies: list[MetricTaxonomy] = []
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

    def _labels(self) -> dict[str, str]:
        try:
            return getattr(self.app, "labels", {}) or {}
        except Exception:
            return {}

    def compose_body(self) -> ComposeResult:
        with Vertical(id="metric-body"):
            with Vertical(classes="pane-container"):
                yield Label("METRIC", classes="pane-header")
                yield Static("", id="metric-fields", markup=True)
                yield Static(
                    "Idle — press [bold]r[/bold] to load",
                    id="metric-status",
                    markup=True,
                )
                yield Label("ENTITIES", id="entities-header")
                yield Input(
                    placeholder="/ search entities for this metric…",
                    id="assoc-filter",
                )
                yield ListView(id="entities-list")

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
        if not ctx.metric:
            self.query_one("#metric-fields", Static).update(
                "No metric in Input Context.\n"
                "Drill into a metric from a table, or stage one via :search."
            )
            self.query_one("#metric-status", Static).update("")
            return
        self._metric_id = ctx.metric[0]
        self._label = label_for(self._labels(), self._metric_id)
        note = ""
        if len(ctx.metric) > 1:
            note = f"\n(+{len(ctx.metric) - 1} more metrics in Input Context; showing first)"
        self._render_fields(self._metric_id, self._label, note=note)
        self.query_one("#entities-list", ListView).focus()

    def _render_fields(
        self,
        metric_id: str,
        label: str,
        *,
        note: str = "",
        metric: Optional[Metric] = None,
        taxonomies: Optional[list[MetricTaxonomy]] = None,
    ) -> None:
        lines = [
            f"[bold]id[/bold]     {metric_id}",
            f"[bold]label[/bold]  {label or '—'}",
        ]
        if metric is not None and metric.description:
            desc = metric.description.strip()
            if len(desc) > 280:
                desc = desc[:277] + "…"
            lines.append(f"[bold]desc[/bold]   {desc}")
        if metric is not None and metric.variants:
            lines.append("[bold]variants[/bold]")
            for variant in metric.variants[:6]:
                freq = variant.frequency or "?"
                sa = variant.seasonal_adjustment or "—"
                lines.append(
                    f"  · {freq} · {variant.source or '—'} · "
                    f"{variant.units or '—'} · sa {sa} · "
                    f"{variant.series_count} series"
                )
            if len(metric.variants) > 6:
                lines.append(f"  · +{len(metric.variants) - 6} more")
        tax = taxonomies if taxonomies is not None else self._taxonomies
        if tax:
            lines.append("[bold]taxonomies[/bold]")
            for row in tax[:5]:
                lines.append(
                    f"  · {row.name or row.id} · {row.relationship} · "
                    f"{row.entity_count} entities"
                )
            if len(tax) > 5:
                lines.append(f"  · +{len(tax) - 5} more")
        if note:
            lines.append(note.lstrip("\n"))
        self.query_one("#metric-fields", Static).update("\n".join(lines))

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold]r[/bold] load"]
        if self._loaded:
            keys.append("[bold]/[/bold] search")
            keys.append("[bold]j[/bold]/[bold]k[/bold] entities (more at end)")
            keys.append("[bold]enter[/bold] entity")
        keys.append("[bold]space[/bold] collect")
        return keys

    def action_load(self) -> None:
        if not self._metric_id:
            self.notify("No metric in Input Context", severity="warning")
            return
        self._filter_query = ""
        self.query_one("#assoc-filter", Input).value = ""
        self.load_metric(reset=True, reset_meta=True)

    def action_focus_filter(self) -> None:
        if not self._loaded:
            self.notify("Press r to load first", severity="warning")
            return
        self.query_one("#assoc-filter", Input).focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#assoc-filter", Input)
        if self.focused is filt:
            self.query_one("#entities-list", ListView).focus()

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self.query_one("#entities-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        lv = self.query_one("#entities-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    def _highlighted_entity(self) -> Optional[Entity]:
        child = self.query_one("#entities-list", ListView).highlighted_child
        if isinstance(child, EntityAssocRow):
            return child.entity
        return None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "collect" and isinstance(self.focused, Input):
            return False
        return True

    def action_collect(self) -> None:
        entity = self._highlighted_entity() if self._loaded else None
        if entity is not None:
            added = stage_ids(
                self.workspace,
                self.app,
                entity=entity.id,
                labels={entity.id: entity.label or entity.id},
            )
            if not added:
                self.notify(f"{entity.id} already in workspace", severity="warning")
                return
            self.notify(f"Collected entity {entity.label or entity.id}")
            return
        if not self._metric_id:
            self.notify("Nothing to collect", severity="warning")
            return
        added = stage_ids(
            self.workspace,
            self.app,
            metric=self._metric_id,
            labels={self._metric_id: self._label or self._metric_id},
        )
        if not added:
            self.notify(f"{self._metric_id} already in workspace", severity="warning")
            return
        self.notify(f"Collected metric {self._label or self._metric_id}")

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        if not self._loaded:
            self.notify("Press r to load entities first", severity="warning")
            return
        entity = self._highlighted_entity()
        if entity is None:
            self.notify("No entity highlighted", severity="warning")
            return
        open_fn = getattr(self.app, "open_child_view", None)
        if open_fn is None:
            self.notify("Host cannot open child views", severity="error")
            return
        open_fn(
            "entity",
            context=focus_replace_context(
                self._parent_overrides, entity=[entity.id]
            ),
            description=f"Entity {entity.label or entity.id}",
        )

    @on(ListView.Selected, "#entities-list")
    def on_entity_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, EntityAssocRow):
            self.action_confirm()

    @on(ListView.Highlighted, "#entities-list")
    def on_entity_highlighted(self, event: ListView.Highlighted) -> None:
        if self.loading_more or not self.has_more:
            return
        list_view = self.query_one("#entities-list", ListView)
        if event.item is None:
            return
        index = list_view.index
        if index is None or index < len(self._entities) - 1:
            return
        self.loading_more = True
        self.page_offset = len(self._entities)
        self.load_metric(reset=False)

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
            self.load_metric(reset=True, reset_meta=False)
        except asyncio.CancelledError:
            pass

    def _append_entities(self, entities: list[Entity]) -> None:
        list_view = self.query_one("#entities-list", ListView)
        for ent in entities:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(ent.id, ent.label or ent.id)
            list_view.append(EntityAssocRow(ent))

    def _update_status(self, query: str | None) -> None:
        status = self.query_one("#metric-status", Static)
        extra = "+" if self.has_more else ""
        qbit = f" · “{query}”" if query else ""
        if self._entities:
            status.update(
                f"{len(self._entities)}{extra} "
                f"entit{'y' if len(self._entities) == 1 else 'ies'}{qbit}"
            )
        else:
            status.update(f"No entities{qbit}")

    @work(exclusive=True, group="metric-view")
    async def load_metric(self, *, reset: bool = True, reset_meta: bool = False) -> None:
        metric_id = self._metric_id
        if not metric_id:
            self.loading_more = False
            return
        if reset:
            self.page_offset = 0
            self.has_more = False
            self.loading_more = False
        status = self.query_one("#metric-status", Static)
        status.update("Loading…" if reset or not self._loaded else "Loading more…")
        query = self._filter_query.strip() or None
        fetch_limit = page_fetch_limit(ASSOCIATES_PAGE)
        try:
            if reset_meta or not self._metric_meta_loaded:
                metric, raw, taxonomies = await asyncio.gather(
                    asyncio.to_thread(self.client.get_metric, metric_id),
                    asyncio.to_thread(
                        self.client.search_entities,
                        query,
                        metric=metric_id,
                        limit=fetch_limit,
                        offset=self.page_offset,
                    ),
                    asyncio.to_thread(
                        self.client.get_metric_taxonomies,
                        metric_id,
                        TAXONOMIES_PAGE,
                        0,
                    ),
                )
                if metric_id != self._metric_id:
                    self.loading_more = False
                    return
                label = metric.name or metric.id
                self._label = label
                self._taxonomies = list(taxonomies)
                if hasattr(self.app, "remember_label"):
                    self.app.remember_label(metric_id, label)
                note = ""
                ctx = self._resolved_input_context()
                if len(ctx.metric) > 1:
                    note = (
                        f"(+{len(ctx.metric) - 1} more metrics in Input Context; "
                        "showing first)"
                    )
                self._render_fields(
                    metric_id,
                    label,
                    note=note,
                    metric=metric,
                    taxonomies=self._taxonomies,
                )
                self._metric_meta_loaded = True
            else:
                raw = await asyncio.to_thread(
                    self.client.search_entities,
                    query,
                    metric=metric_id,
                    limit=fetch_limit,
                    offset=self.page_offset,
                )
                if metric_id != self._metric_id:
                    self.loading_more = False
                    return
            entities, has_more = take_page(raw, ASSOCIATES_PAGE)
        except Exception as exc:
            status.update(f"Could not load metric: {exc}")
            self.loading_more = False
            return

        if reset:
            self._entities = []
            self.query_one("#entities-list", ListView).clear()
        self._entities.extend(entities)
        self.has_more = has_more
        self._loaded = True
        self.loading_more = False
        self._append_entities(entities)
        self._update_status(query)
        self.refresh_footer()


def create_metric_screen(client, workspace, input_context=None, **kwargs):
    return MetricScreen(client, workspace, input_context=input_context)


METRIC_VIEW = register(
    ActionSpec(
        id="metric",
        name="Metric",
        description=(
            "Inspect a focused metric (description, variants, taxonomies, "
            "covered entities). Idle until load (r); / searches entities; "
            "j/k loads more at the end of the list."
        ),
        create_screen=create_metric_screen,
        arguments=(),
        bindings=(
            ActionBinding("r", "load", "Load metric details and covered entities"),
            ActionBinding("/", "focus_filter", "Search entities for this metric"),
            ActionBinding("j / k", "cursor", "Move in entities list (loads more at end)"),
            ActionBinding("enter", "confirm", "Open entity view for highlighted row"),
            ActionBinding(
                "space",
                "collect",
                "Collect highlighted entity (or this metric) into the workspace",
            ),
        ),
        example="jst action add metric",
    )
)
