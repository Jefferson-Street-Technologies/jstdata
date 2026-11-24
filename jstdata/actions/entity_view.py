"""Entity View — metadata, associated metrics, and relationship edges.

No network until the user presses ``r`` (load). ``/`` searches metrics.
j/k on the last metrics row fetches the next page.

Relationship filters (Data API):
* ``direction`` — outgoing (default) or incoming relative to this entity
* ``relationship`` — edge type (never ``identity``; use membership taxonomy)
* ``relation_taxonomy`` — edge taxonomy slug
* ``taxonomy`` — membership taxonomy of the *related* entity
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
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    label_for,
    register,
    stage_ids,
)
from .pagination import page_fetch_limit, take_page
from .screen import BaseScreen

RELATIONS_PAGE = 50
ASSOCIATES_PAGE = 50
SEARCH_DEBOUNCE_S = 0.3

Pane = Literal["metrics", "relations"]
RelationDirection = Literal["outgoing", "incoming"]
RELATIONSHIP_TYPES = (
    "classified_as",
    "equivalent_to",
    "part_of",
    "has_security",
)


class MetricAssocRow(ListItem):
    def __init__(self, metric: Metric) -> None:
        super().__init__()
        self.metric = metric

    def compose(self) -> ComposeResult:
        name = self.metric.name or self.metric.id
        yield Label(f"{name}  [{self.metric.id}]")


class RelationRow(ListItem):
    def __init__(
        self, rel: EntityRelationship, *, direction: RelationDirection = "outgoing"
    ) -> None:
        super().__init__()
        self.rel = rel
        self.direction = direction

    def compose(self) -> ComposeResult:
        tax = f"  [{self.rel.taxonomy}]" if self.rel.taxonomy else ""
        rel_name = (
            self.rel.relationship.value
            if hasattr(self.rel.relationship, "value")
            else str(self.rel.relationship)
        )
        # Outgoing: this → related. Incoming: related → this (arrow toward self).
        arrow = "→" if self.direction == "outgoing" else "←"
        crosswalk = (
            "  (crosswalk)"
            if rel_name == "equivalent_to"
            else ""
        )
        yield Label(f"{rel_name:<16} {arrow} {self.rel.id}{tax}{crosswalk}")


class EntityScreen(BaseScreen):
    """Show id/label, associated metrics, and relations for one entity."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("slash", "focus_filter", "Search metrics", key_display="/"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open metric / related entity", priority=True),
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
        Binding("d", "toggle_direction", "Toggle relation direction", show=False),
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
        direction: str | None = None,
        relationship: str | None = None,
        relation_taxonomy: str | None = None,
        taxonomy: str | None = None,
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
        self.page_offset = 0
        self.has_more = False
        self.loading_more = False
        direction_norm = (direction or "outgoing").strip().lower()
        self.direction: RelationDirection = (
            "incoming" if direction_norm == "incoming" else "outgoing"
        )
        rel_type = (relationship or "").strip() or None
        if rel_type == "identity":
            rel_type = None  # membership is taxonomy=, not a relation type
        if rel_type and rel_type not in RELATIONSHIP_TYPES:
            rel_type = None
        self.relationship = rel_type
        self.relation_taxonomy = (relation_taxonomy or "").strip() or None
        self.related_membership_taxonomy = (taxonomy or "").strip() or None

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
                yield Label(
                    "RELATIONSHIPS (outgoing)",
                    id="relations-header",
                )
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
            keys.append(f"[bold]d[/bold] {self.direction}")
            keys.append("[bold]j[/bold]/[bold]k[/bold] move")
            if self._pane == "metrics":
                keys.append("[bold]enter[/bold] metric")
                keys.append("[bold]space[/bold] collect metric")
            else:
                keys.append("[bold]enter[/bold] related entity")
        keys.append("[bold]a[/bold] add entity")
        return keys

    def footer_status(self) -> list[str]:
        parts = super().footer_status()
        parts.append(f"rels {self.direction}")
        if self.relationship:
            parts.append(self.relationship)
        if self.relation_taxonomy:
            parts.append(f"edge-tax {self.relation_taxonomy}")
        if self.related_membership_taxonomy:
            parts.append(f"member-tax {self.related_membership_taxonomy}")
        return parts

    def _relations_header_text(self) -> str:
        bits = [f"RELATIONSHIPS ({self.direction})"]
        if self.relationship:
            bits.append(self.relationship)
        if self.relation_taxonomy:
            bits.append(f"edge:{self.relation_taxonomy}")
        if self.related_membership_taxonomy:
            bits.append(f"members:{self.related_membership_taxonomy}")
        return " · ".join(bits)

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
        self.load_entity(reset=True, reset_meta=True)

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

    def action_toggle_direction(self) -> None:
        """Flip relation direction and reload edges (membership unchanged)."""
        self.direction = (
            "incoming" if self.direction == "outgoing" else "outgoing"
        )
        self.query_one("#relations-header", Label).update(
            self._relations_header_text()
        )
        self.refresh_footer()
        if self._loaded and self._entity_id:
            self._reload_relations()

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

    def _highlighted_relation(self) -> Optional[EntityRelationship]:
        child = self.query_one("#relations-list", ListView).highlighted_child
        if isinstance(child, RelationRow):
            return child.rel
        return None

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        if not self._loaded:
            self.notify("Press r to load metrics first", severity="warning")
            return
        open_fn = getattr(self.app, "open_child_view", None)
        if open_fn is None:
            self.notify("Host cannot open child views", severity="error")
            return
        if self._pane == "relations":
            rel = self._highlighted_relation()
            if rel is None:
                self.notify("No related entity highlighted", severity="warning")
                return
            open_fn(
                "entity",
                context=focus_replace_context(
                    self._parent_overrides, entity=[rel.id]
                ),
                description=f"Entity {rel.id}",
            )
            return
        metric = self._highlighted_metric()
        if metric is None:
            self.notify("No metric highlighted", severity="warning")
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

    @on(ListView.Selected, "#relations-list")
    def on_relation_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, RelationRow):
            self._pane = "relations"
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
        self.load_entity(reset=False, reset_meta=False)

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
            self.load_entity(reset=True, reset_meta=False)
        except asyncio.CancelledError:
            pass

    def _append_metrics(self, metrics: list[Metric]) -> None:
        metrics_list = self.query_one("#metrics-list", ListView)
        for metric in metrics:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(metric.id, metric.name or metric.id)
            metrics_list.append(MetricAssocRow(metric))

    def _show_relations(self, relations: list[EntityRelationship]) -> None:
        self._relations = list(relations)
        rel_list = self.query_one("#relations-list", ListView)
        rel_list.clear()
        for rel in self._relations:
            rel_list.append(RelationRow(rel, direction=self.direction))
        self.query_one("#relations-header", Label).update(
            self._relations_header_text()
        )

    def _relation_kwargs(self) -> dict:
        kwargs: dict = {
            "direction": self.direction,
            "limit": RELATIONS_PAGE,
            "offset": 0,
        }
        if self.relationship:
            kwargs["relationship"] = self.relationship
        if self.relation_taxonomy:
            kwargs["relation_taxonomy"] = self.relation_taxonomy
        if self.related_membership_taxonomy:
            kwargs["taxonomy"] = self.related_membership_taxonomy
        return kwargs

    def _update_status(self, query: str | None) -> None:
        status = self.query_one("#entity-status", Static)
        m_extra = "+" if self.has_more else ""
        r_extra = "+" if len(self._relations) >= RELATIONS_PAGE else ""
        qbit = f" · “{query}”" if query else ""
        status.update(
            f"{len(self._metrics)}{m_extra} metric(s){qbit} · "
            f"{len(self._relations)}{r_extra} {self.direction} relationship(s) "
            f"(membership ≠ classification; d toggles direction)"
        )

    @work(exclusive=True, group="entity-relations")
    async def _reload_relations(self) -> None:
        entity_id = self._entity_id
        if not entity_id:
            return
        status = self.query_one("#entity-status", Static)
        status.update(f"Loading {self.direction} relationships…")
        try:
            relations = await asyncio.to_thread(
                self.client.get_entity_relations,
                entity_id,
                **self._relation_kwargs(),
            )
        except Exception as exc:
            status.update(f"Could not load relations: {exc}")
            return
        if entity_id != self._entity_id:
            return
        self._show_relations(list(relations))
        self._update_status(self._filter_query.strip() or None)
        self.refresh_footer()

    @work(exclusive=True, group="entity-view")
    async def load_entity(
        self, *, reset: bool = True, reset_meta: bool = True
    ) -> None:
        entity_id = self._entity_id
        if not entity_id:
            self.loading_more = False
            return
        if reset:
            self.page_offset = 0
            self.has_more = False
            self.loading_more = False
        status = self.query_one("#entity-status", Static)
        status.update("Loading…" if reset or not self._loaded else "Loading more…")
        query = self._filter_query.strip() or None
        fetch_limit = page_fetch_limit(ASSOCIATES_PAGE)
        try:
            if reset_meta or not self._entity_meta_loaded:
                entity, relations, raw = await asyncio.gather(
                    asyncio.to_thread(self.client.get_entity, entity_id),
                    asyncio.to_thread(
                        self.client.get_entity_relations,
                        entity_id,
                        **self._relation_kwargs(),
                    ),
                    asyncio.to_thread(
                        self.client.search_metrics,
                        query,
                        entity=entity_id,
                        limit=fetch_limit,
                        offset=self.page_offset,
                    ),
                )
                if entity_id != self._entity_id:
                    self.loading_more = False
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
                raw = await asyncio.to_thread(
                    self.client.search_metrics,
                    query,
                    entity=entity_id,
                    limit=fetch_limit,
                    offset=self.page_offset,
                )
                if entity_id != self._entity_id:
                    self.loading_more = False
                    return
            metrics, has_more = take_page(raw, ASSOCIATES_PAGE)
        except Exception as exc:
            status.update(f"Could not load entity: {exc}")
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
        self._update_status(query)
        self.refresh_footer()


def create_entity_screen(
    client,
    workspace,
    input_context=None,
    direction=None,
    relationship=None,
    relation_taxonomy=None,
    taxonomy=None,
    **kwargs,
):
    return EntityScreen(
        client,
        workspace,
        input_context=input_context,
        direction=direction,
        relationship=relationship,
        relation_taxonomy=relation_taxonomy,
        taxonomy=taxonomy,
    )


ENTITY_VIEW = register(
    ActionSpec(
        id="entity",
        name="Entity",
        description=(
            "Inspect a focused entity (id, label, associated metrics, "
            "relationships). Idle until load (r); / searches metrics; "
            "d toggles relation direction. Membership (taxonomy) ≠ "
            "classification (classified_as)."
        ),
        create_screen=create_entity_screen,
        arguments=(
            ActionArgument(
                name="direction",
                type="string",
                description="Relation direction relative to this entity",
                choices=("outgoing", "incoming"),
            ),
            ActionArgument(
                name="relationship",
                type="string",
                description=(
                    "Edge type filter (not identity — use membership taxonomy "
                    "for that)"
                ),
                choices=RELATIONSHIP_TYPES,
            ),
            ActionArgument(
                name="relation_taxonomy",
                type="string",
                description="Edge taxonomy slug (e.g. standard-industrial-classification-sic)",
            ),
            ActionArgument(
                name="taxonomy",
                type="string",
                description=(
                    "Membership taxonomy of related entities "
                    "(e.g. us-county); distinct from edge taxonomy"
                ),
            ),
        ),
        bindings=(
            ActionBinding("r", "load", "Load entity details, metrics, and relations"),
            ActionBinding("/", "focus_filter", "Search metrics for this entity"),
            ActionBinding("tab", "cycle_pane", "Switch metrics / relationships lists"),
            ActionBinding(
                "d",
                "toggle_direction",
                "Toggle relation direction (outgoing ↔ incoming) and reload",
            ),
            ActionBinding(
                "j / k",
                "cursor",
                "Move in the active list (loads more metrics at the bottom)",
            ),
            ActionBinding(
                "enter",
                "confirm",
                "Open metric (metrics pane) or related entity (relations pane)",
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
        example=(
            "jst actions relationships sic:3674 --direction incoming "
            "--relationship classified_as"
        ),
    )
)
