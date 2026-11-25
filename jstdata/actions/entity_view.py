"""Entity View — metadata, associated metrics, and relationship edges.

No network until the user presses ``r`` (load). ``/`` searches metrics.
j/k on the last metrics row fetches the next page.

Relationships load in both directions (outgoing and incoming) and list together.

Relationship filters (Data API):
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

# Client-side predicate → human group labels (no API / model change).
_RELATION_LABELS: dict[tuple[str, RelationDirection], str] = {
    ("part_of", "outgoing"): "Part of",
    ("part_of", "incoming"): "Contains",
    ("classified_as", "outgoing"): "Classified as",
    ("classified_as", "incoming"): "Classified entities",
    ("has_security", "outgoing"): "Securities",
    ("has_security", "incoming"): "Issuer",
    ("equivalent_to", "outgoing"): "Equivalent to",
    ("equivalent_to", "incoming"): "Equivalent to",
}

_DEMOTE_METRIC_TOKENS = (
    "confidence interval",
    "upper bound",
    "lower bound",
    "standard error",
    "margin of error",
    "coefficient of variation",
    "90 percent",
    "95 percent",
)


def _relation_group_label(rel_name: str, direction: RelationDirection) -> str:
    return _RELATION_LABELS.get(
        (rel_name, direction),
        f"{rel_name.replace('_', ' ').title()} ({direction})",
    )


def _metric_browse_key(metric: Metric) -> tuple[int, str]:
    """Demote auxiliary statistical measures in the default ordering."""
    name = (metric.name or metric.id or "").lower()
    demote = any(tok in name for tok in _DEMOTE_METRIC_TOKENS)
    return (1 if demote else 0, name)


class MetricAssocRow(ListItem):
    """Multi-line metric card — name plus every variant field we have."""

    def __init__(self, metric: Metric) -> None:
        super().__init__()
        self.metric = metric

    def compose(self) -> ComposeResult:
        m = self.metric
        name = m.name or m.id
        yield Label(name, classes="assoc-name")
        if m.variants:
            v = m.variants[0]
            bits: list[str] = []
            if v.units:
                bits.append(v.units)
            if v.frequency:
                bits.append(v.frequency)
            if v.source:
                bits.append(v.source)
            if v.series_count:
                bits.append(
                    f"{v.series_count} series"
                    if v.series_count != 1
                    else "1 series"
                )
            if v.seasonal_adjustment:
                bits.append(v.seasonal_adjustment)
            extra = len(m.variants) - 1
            if extra > 0:
                bits.append(f"+{extra} variant{'s' if extra != 1 else ''}")
            if bits:
                yield Label(" · ".join(bits), classes="assoc-meta")
        if m.description:
            desc = m.description.strip().replace("\n", " ")
            if len(desc) > 100:
                desc = desc[:97] + "…"
            yield Label(desc, classes="assoc-desc")
        yield Label(m.id, classes="assoc-id")


class RelationRow(ListItem):
    def __init__(
        self,
        rel: EntityRelationship,
        *,
        direction: RelationDirection = "outgoing",
        label: str | None = None,
    ) -> None:
        super().__init__()
        self.rel = rel
        self.direction = direction
        self.display_label = label or rel.id

    def compose(self) -> ComposeResult:
        yield Label(f"  {self.display_label}")


class EntityScreen(BaseScreen):
    """Show id/label, associated metrics, and relations for one entity."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("f", "focus_filter", "Search metrics", key_display="f"),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open chart / related entity", priority=True),
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
        Binding("m", "focus_metrics", "Metrics tab", show=False),
        Binding("R", "focus_relations", "Relations tab", show=False),
        Binding("space", "collect", "Collect highlighted metric", show=False),
        Binding("a", "collect_self", "Add this entity to workspace", show=False),
    ]

    DEFAULT_CSS = """
    #entity-body {
        height: 1fr;
        padding: 0 1;
    }
    #entity-title {
        color: #f5f5f5;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #entity-meta {
        color: #888;
        height: 1;
    }
    #entity-actions {
        color: #a0a0a0;
        height: 1;
        margin-bottom: 1;
    }
    #entity-tabs {
        height: 1;
        margin-bottom: 0;
    }
    #entity-status {
        color: #666;
        height: 1;
    }
    #assoc-filter {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 1;
    }
    #metrics-list, #relations-list {
        height: 1fr;
        background: #0c0c0c;
    }
    MetricAssocRow {
        height: auto;
        min-height: 4;
        max-height: 6;
        padding: 1 1;
        border-left: solid #0c0c0c;
        background: transparent;
        color: #e0e0e0;
    }
    MetricAssocRow.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    RelationRow {
        height: 2;
        min-height: 2;
        max-height: 2;
        padding: 0 1;
        border-left: solid #0c0c0c;
    }
    RelationRow.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    .assoc-name {
        height: 1;
        color: #f5f5f5;
        text-style: bold;
    }
    .assoc-meta {
        height: 1;
        color: #a8a8a8;
    }
    .assoc-desc {
        height: 1;
        color: #888;
    }
    .assoc-id {
        height: 1;
        color: #555;
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
        # (direction, edge) — both outgoing and incoming are shown together.
        self._relations: list[tuple[RelationDirection, EntityRelationship]] = []
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
        # Kept for ActionSpec / agent args; UI always loads both directions.
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
            yield Label("", id="entity-title")
            yield Static("", id="entity-meta", markup=True)
            yield Static(
                "[bold #86efac]View chart[/] ↵   "
                "[bold #86efac]Collect metric[/] space   "
                "[dim]a[/] this entity",
                id="entity-actions",
                markup=True,
            )
            yield Static("", id="entity-tabs", markup=True)
            yield Static(
                "Press [bold]r[/bold] to load metrics & relationships",
                id="entity-status",
                markup=True,
            )
            yield Input(
                placeholder="/ search metrics…",
                id="assoc-filter",
            )
            yield ListView(id="metrics-list")
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
            self.query_one("#entity-title", Label).update("No entity selected")
            self.query_one("#entity-meta", Static).update(
                "Search [/] and open an entity, or stage one into the workspace."
            )
            self.query_one("#entity-status", Static).update("")
            return
        self._entity_id = ctx.entity[0]
        self._label = label_for(self._labels(), self._entity_id)
        note = ""
        if len(ctx.entity) > 1:
            note = f" (+{len(ctx.entity) - 1} more in context)"
        self._render_fields(note=note)
        self._focus_pane("metrics")
        # Auto-load — discovery screen should not wait on r.
        # Prefer disk cache on first open; explicit ``r`` refreshes from network.
        self.call_later(
            lambda: self.load_entity(reset=True, reset_meta=True, refresh=False)
        )

    def _render_fields(self, *, note: str = "") -> None:
        self.query_one("#entity-title", Label).update(self._label or self._entity_id or "—")
        meta = "Entity"
        if note:
            meta += note
        self.query_one("#entity-meta", Static).update(meta)
        self._refresh_tabs()

    def shell_breadcrumb(self) -> str:
        return self._label or self._entity_id or "Entity"

    def bug_report_snapshot(self) -> dict:
        from .pagination import page_fetch_limit

        query = self._filter_query.strip() or None
        fetch_limit = page_fetch_limit(ASSOCIATES_PAGE)
        entity_id = self._entity_id
        return {
            "kind": "entity",
            "entity_id": entity_id,
            "label": self._label,
            "loaded": self._loaded,
            "filter": query,
            "page_offset": self.page_offset,
            "visible_metric_ids": [m.id for m in self._metrics],
            "visible_relation_ids": [r.id for _, r in self._relations],
            "api": {
                "client.get_entity": {"id": entity_id},
                "client.search_metrics": {
                    "q": query,
                    "entity": entity_id,
                    "limit": fetch_limit,
                    "offset": self.page_offset if self._loaded else 0,
                },
                "client.get_entity_relations": {
                    "entity_id": entity_id,
                    **self._relation_filter_kwargs(),
                    "directions": ["outgoing", "incoming"],
                },
            },
        }

    def bug_report_summary(self) -> str:
        eid = self._entity_id or "—"
        return f"[dim]Entity[/] [bold]{eid}[/] [dim]→ get_entity + search_metrics + relations[/]"

    def footer_action_keys(self) -> list[str]:
        keys: list[str] = []
        if self._loaded:
            keys.append("[bold]tab[/bold] metrics/rels")
            keys.append("[bold]/[/bold] filter")
            if self._pane == "metrics":
                keys.append("[bold]enter[/bold] chart")
                keys.append("[bold]space[/bold] collect")
            else:
                keys.append("[bold]enter[/bold] open")
        else:
            keys.append("[bold]r[/bold] load")
        keys.append("[bold]a[/bold] workspace")
        return keys

    def footer_status(self) -> list[str]:
        return []

    def _refresh_tabs(self) -> None:
        n_m = len(self._metrics)
        n_r = len(self._relations)
        m_extra = "+" if self.has_more else ""
        if self._pane == "metrics":
            text = (
                f"[bold #86efac on #163324] Metrics ({n_m}{m_extra}) [/]  "
                f"[#888]Relationships ({n_r})[/]"
            )
        else:
            text = (
                f"[#888]Metrics ({n_m}{m_extra})[/]  "
                f"[bold #86efac on #163324] Relationships ({n_r}) [/]"
            )
        try:
            self.query_one("#entity-tabs", Static).update(text)
        except Exception:  # noqa: BLE001
            pass

    def _active_list(self) -> ListView:
        if self._pane == "metrics":
            return self.query_one("#metrics-list", ListView)
        return self.query_one("#relations-list", ListView)

    def _focus_pane(self, pane: Pane) -> None:
        self._pane = pane
        metrics = self.query_one("#metrics-list", ListView)
        relations = self.query_one("#relations-list", ListView)
        filt = self.query_one("#assoc-filter", Input)
        metrics.display = pane == "metrics"
        relations.display = pane == "relations"
        filt.display = pane == "metrics"
        self._refresh_tabs()
        self._active_list().focus()
        self.refresh_footer()

    def action_focus_metrics(self) -> None:
        self._focus_pane("metrics")

    def action_focus_relations(self) -> None:
        self._focus_pane("relations")

    def action_load(self) -> None:
        if not self._entity_id:
            self.notify("No entity in Input Context", severity="warning")
            return
        self._filter_query = ""
        self.query_one("#assoc-filter", Input).value = ""
        # ``r`` forces a network refresh; pagination / filter reuse disk cache.
        self.load_entity(reset=True, reset_meta=True, refresh=True)

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
        return super().check_action(action, parameters)

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
        # Chart-first: Enter on a metric opens a draft query chart immediately.
        chart_fn = getattr(self.app, "open_direct_query_chart", None)
        entity_ids = [self._entity_id] if self._entity_id else []
        if callable(chart_fn) and entity_ids:
            chart_fn(metric_ids=[metric.id], entity_ids=entity_ids)
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
        ordered = sorted(metrics, key=_metric_browse_key)
        for metric in ordered:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(metric.id, metric.name or metric.id)
            metrics_list.append(MetricAssocRow(metric))

    def _show_relations(
        self,
        *,
        outgoing: list[EntityRelationship],
        incoming: list[EntityRelationship],
    ) -> None:
        self._relations = [
            *[("outgoing", rel) for rel in outgoing],
            *[("incoming", rel) for rel in incoming],
        ]
        labels = self._labels()
        rel_list = self.query_one("#relations-list", ListView)
        rel_list.clear()

        def _rel_key(rel: EntityRelationship) -> str:
            return (
                rel.relationship.value
                if hasattr(rel.relationship, "value")
                else str(rel.relationship)
            )

        # Group by human predicate label, preserving direction semantics.
        groups: list[tuple[str, RelationDirection, list[EntityRelationship]]] = []
        for direction, bucket in (
            ("outgoing", outgoing),
            ("incoming", incoming),
        ):
            by_pred: dict[str, list[EntityRelationship]] = {}
            for rel in bucket:
                by_pred.setdefault(_rel_key(rel), []).append(rel)
            for pred, rows in by_pred.items():
                rows_sorted = sorted(
                    rows,
                    key=lambda r: (label_for(labels, r.id) or r.id).lower(),
                )
                groups.append(
                    (_relation_group_label(pred, direction), direction, rows_sorted)
                )

        if not groups:
            rel_list.append(
                ListItem(Label("[dim](no relationships)[/]", markup=True))
            )
        else:
            for heading, direction, rows in groups:
                rel_list.append(
                    ListItem(
                        Label(
                            f"[bold]{heading}[/]  [dim]({len(rows)})[/]",
                            markup=True,
                        )
                    )
                )
                for rel in rows:
                    display = label_for(labels, rel.id) or rel.id
                    # Prefer remembered labels; slug fallback is last resort.
                    if display == rel.id and "-" in rel.id:
                        display = rel.id.replace("-", " ").title()
                    rel_list.append(
                        RelationRow(rel, direction=direction, label=display)
                    )
        self._refresh_tabs()

    def _relation_filter_kwargs(self) -> dict:
        """Shared filters for both direction fetches (no direction key)."""
        kwargs: dict = {
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

    def _fetch_relations_both(
        self, entity_id: str
    ) -> tuple[list[EntityRelationship], list[EntityRelationship]]:
        base = self._relation_filter_kwargs()
        outgoing = self.client.get_entity_relations(
            entity_id, direction="outgoing", **base
        )
        incoming = self.client.get_entity_relations(
            entity_id, direction="incoming", **base
        )
        return list(outgoing), list(incoming)

    async def _resolve_relation_labels(
        self, relations: list[EntityRelationship]
    ) -> None:
        """Fill display labels for related entities via existing get_entity."""
        labels = self._labels()
        missing = [
            rel.id for rel in relations if rel.id and rel.id not in labels
        ]
        # Cap lookups so a large Contains group doesn't stall the screen.
        missing = missing[:40]
        if not missing:
            return

        async def _one(rid: str) -> None:
            try:
                ent = await asyncio.to_thread(self.client.get_entity, rid)
            except Exception:  # noqa: BLE001
                return
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(rid, ent.label or rid)

        await asyncio.gather(*[_one(rid) for rid in missing])

    def _update_status(self, query: str | None) -> None:
        status = self.query_one("#entity-status", Static)
        m_extra = "+" if self.has_more else ""
        n_out = sum(1 for d, _ in self._relations if d == "outgoing")
        n_in = sum(1 for d, _ in self._relations if d == "incoming")
        r_extra = (
            "+"
            if n_out >= RELATIONS_PAGE or n_in >= RELATIONS_PAGE
            else ""
        )
        qbit = f" · “{query}”" if query else ""
        status.update(
            f"{len(self._metrics)}{m_extra} metrics{qbit} · "
            f"{n_out + n_in}{r_extra} relationships (→{n_out} · ←{n_in})"
        )
        self._refresh_tabs()
        meta = self.query_one("#entity-meta", Static)
        meta.update(
            f"{len(self._metrics)}{m_extra} metrics · "
            f"{n_out + n_in}{r_extra} relationships"
        )

    def _with_client_cache(self, refresh: bool, fn, /, *args, **kwargs):
        if refresh and hasattr(self.client, "uncached"):
            with self.client.uncached():
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)

    @work(exclusive=True, group="entity-view")
    async def load_entity(
        self,
        *,
        reset: bool = True,
        reset_meta: bool = True,
        refresh: bool = False,
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
                entity, relations_pair, raw = await asyncio.gather(
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
                        self.client.get_entity,
                        entity_id,
                    ),
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
                        self._fetch_relations_both,
                        entity_id,
                    ),
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
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
                outgoing, incoming = relations_pair
                await self._resolve_relation_labels(outgoing + incoming)
                self._show_relations(outgoing=outgoing, incoming=incoming)
                self._entity_meta_loaded = True
            else:
                raw = await asyncio.to_thread(
                    self._with_client_cache,
                    refresh,
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
            "relationships show both outgoing and incoming. Membership "
            "(taxonomy) ≠ classification (classified_as)."
        ),
        create_screen=create_entity_screen,
        arguments=(
            ActionArgument(
                name="direction",
                type="string",
                description=(
                    "Legacy; UI always loads both outgoing and incoming"
                ),
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
