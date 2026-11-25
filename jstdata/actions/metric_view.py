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
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import (
    ResearchContext,
    Workspace,
    focus_replace_context,
)
from ..investigation.ui_primitives import ActionButton
from ..models import Entity, Metric, MetricTaxonomy, MetricVariant
from .base import ActionBinding, ActionSpec, label_for, register, stage_ids
from .pagination import page_fetch_limit, take_page
from .screen import BaseScreen

ASSOCIATES_PAGE = 50
TAXONOMIES_PAGE = 100
SEARCH_DEBOUNCE_S = 0.3


class EntityAssocRow(ListItem):
    """Multi-line entity card — label plus every field we have for the row."""

    def __init__(
        self,
        entity: Entity,
        *,
        frequency: str = "",
        units: str = "",
        source: str = "",
        series_count: int = 0,
    ) -> None:
        super().__init__()
        self.entity = entity
        self.frequency = frequency
        self.units = units
        self.source = source
        self.series_count = series_count

    def compose(self) -> ComposeResult:
        name = self.entity.label or self.entity.id
        yield Label(name, classes="assoc-name")
        bits: list[str] = []
        if self.frequency:
            bits.append(self.frequency)
        if self.units:
            bits.append(self.units)
        if self.source:
            bits.append(self.source)
        if self.series_count:
            bits.append(
                f"{self.series_count} series"
                if self.series_count != 1
                else "1 series"
            )
        if bits:
            yield Label(" · ".join(bits), classes="assoc-meta")
        yield Label(self.entity.id, classes="assoc-id")


class MetricScreen(BaseScreen):
    """Show metric focus plus covered entities — inverse of Entity view."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("f", "focus_filter", "Search entities", key_display="f"),
        # Prefer in-context filter over the shell universal-search popup.
        Binding("slash", "focus_filter", "Search entities", show=False, priority=True),
        Binding("escape", "leave_filter", "Leave search", show=False),
        Binding("enter", "confirm", "Open chart", priority=True),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("tab", "cycle_pane", "Definition / entities", priority=True),
        Binding("d", "focus_definition", "Definition tab", show=False),
        Binding("e", "focus_entities", "Entities tab", show=False),
        Binding("space", "collect", "Collect highlighted entity", show=False),
        Binding("a", "collect_self", "Add this metric to workspace", show=False),
    ]

    DEFAULT_CSS = """
    #metric-body {
        height: 1fr;
        padding: 0 1;
    }
    #metric-title {
        color: #f5f5f5;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    #metric-meta {
        color: #888;
        height: 1;
    }
    #metric-actions {
        height: 1;
        color: #a8a8a8;
        margin: 0 0 0 0;
    }
    #metric-tabs {
        height: 1;
        margin: 1 0 0 0;
        background: #0a0a0a;
    }
    #metric-tabs ActionButton.-tab {
        color: #e5e5e5;
        background: #333333;
    }
    #metric-tabs ActionButton.-tab.-active {
        color: #0a0a0a;
        background: #86efac;
        text-style: bold;
    }
    #metric-status {
        color: #666;
        height: 1;
    }
    #assoc-filter-wrap {
        height: 3;
        margin: 0 0 1 0;
        display: none;
        background: #141414;
    }
    #assoc-filter-wrap.-visible {
        display: block;
    }
    #assoc-filter-kicker {
        width: auto;
        color: #86efac;
        text-style: bold;
        padding: 0 1;
        height: 3;
        content-align: left middle;
    }
    #assoc-filter {
        width: 1fr;
        height: 3;
        background: #1a1a1a;
        border: tall #404040;
        padding: 0 1;
        color: #f5f5f5;
    }
    #assoc-filter:focus {
        border: tall #86efac;
    }
    #entities-list {
        height: 1fr;
        background: #0c0c0c;
        display: none;
    }
    #metric-definition {
        height: 1fr;
        color: #c0c0c0;
        padding: 0 1;
    }
    #metric-def-desc-wrap {
        height: auto;
        max-height: 8;
        margin: 1 0 0 0;
    }
    #metric-def-desc {
        height: auto;
        color: #d4d4d4;
    }
    #metric-variants-label, #metric-taxonomies-label {
        height: 1;
        color: #f5f5f5;
        text-style: bold;
        margin-top: 1;
    }
    #metric-variants {
        height: auto;
        color: #d4d4d4;
        background: #0c0c0c;
        padding: 0 1;
    }
    #metric-taxonomies {
        height: 1fr;
        min-height: 5;
        background: #0c0c0c;
    }
    DataTable > .datatable--header {
        background: #1a1a1a;
        color: #86efac;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #1a3a2a;
        color: #f5f5f5;
    }
    EntityAssocRow {
        height: auto;
        min-height: 3;
        max-height: 5;
        padding: 1 1;
        border-left: solid #0c0c0c;
        background: transparent;
        color: #e0e0e0;
    }
    EntityAssocRow.--highlight {
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
        self._taxonomy_filter: Optional[str] = None
        self._search_task: asyncio.Task | None = None
        self._metric_meta_loaded = False
        self._taxonomies: list[MetricTaxonomy] = []
        self._variants: list[MetricVariant] = []
        self._metric_obj: Optional[Metric] = None
        self._pane: str = "definition"
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
            yield Label("", id="metric-title")
            yield Static("", id="metric-meta", markup=True)
            yield Static(
                "[bold #86efac]View chart[/] ↵   "
                "[bold #86efac]Collect entity[/] space   "
                "[dim]a[/] this metric",
                id="metric-actions",
                markup=True,
            )
            with Horizontal(id="metric-tabs"):
                yield ActionButton(
                    "Definition",
                    action_id="focus_definition",
                    shortcut="d",
                    variant="tab",
                    id="metric-tab-definition",
                )
                yield ActionButton(
                    "Entities",
                    action_id="focus_entities",
                    shortcut="e",
                    variant="tab",
                    id="metric-tab-entities",
                )
            yield Static(
                "Press [bold]r[/bold] to load",
                id="metric-status",
                markup=True,
            )
            with Horizontal(id="assoc-filter-wrap"):
                yield Label("/ ", id="assoc-filter-kicker")
                yield Input(
                    placeholder="Filter entities in this metric…",
                    id="assoc-filter",
                )
            yield ListView(id="entities-list")
            with Vertical(id="metric-definition"):
                with VerticalScroll(id="metric-def-desc-wrap"):
                    yield Static("", id="metric-def-desc", markup=True)
                yield Label("Variants", id="metric-variants-label")
                yield Static("", id="metric-variants", markup=True)
                yield Label("Taxonomies", id="metric-taxonomies-label")
                yield DataTable(
                    id="metric-taxonomies",
                    cursor_type="row",
                    zebra_stripes=True,
                )

    def on_mount(self) -> None:
        super().on_mount()
        taxonomies = self.query_one("#metric-taxonomies", DataTable)
        taxonomies.add_columns("Taxonomy", "Entities", "Id")
        inv = getattr(self.app, "investigation", None)
        active = getattr(self.app, "active", None)
        if inv is not None and active is not None:
            try:
                self._parent_overrides = inv.get_action(active.action).context
            except Exception:
                self._parent_overrides = None
        ctx = self._resolved_input_context()
        if not ctx.metric:
            self.query_one("#metric-title", Label).update("No metric selected")
            self.query_one("#metric-meta", Static).update(
                "Search [/] and open a metric, or stage one into the workspace."
            )
            self.query_one("#metric-status", Static).update("")
            return
        self._metric_id = ctx.metric[0]
        self._label = label_for(self._labels(), self._metric_id)
        note = ""
        if len(ctx.metric) > 1:
            note = f" (+{len(ctx.metric) - 1} more in context)"
        self._render_fields(self._metric_id, self._label, note=note)
        self._focus_pane("definition")
        # Prefer disk cache on first open; explicit ``r`` refreshes from network.
        self.call_later(
            lambda: self.load_metric(reset=True, reset_meta=True, refresh=False)
        )

    def shell_breadcrumb(self) -> str:
        return self._label or self._metric_id or "Metric"

    def bug_report_snapshot(self) -> dict:
        from .pagination import page_fetch_limit

        query = self._filter_query.strip() or None
        fetch_limit = page_fetch_limit(ASSOCIATES_PAGE)
        metric_id = self._metric_id
        return {
            "kind": "metric",
            "metric_id": metric_id,
            "label": self._label,
            "loaded": self._loaded,
            "filter": query,
            "taxonomy_filter": self._taxonomy_filter,
            "page_offset": self.page_offset,
            "visible_entity_ids": [e.id for e in self._entities],
            "api": {
                "client.get_metric": {"id": metric_id},
                "client.get_metric_taxonomies": {
                    "metric_id": metric_id,
                    "limit": TAXONOMIES_PAGE,
                    "offset": 0,
                },
                "client.search_entities": {
                    "q": query,
                    "metric": metric_id,
                    "taxonomy": self._taxonomy_filter,
                    "limit": fetch_limit,
                    "offset": self.page_offset if self._loaded else 0,
                },
            },
        }

    def bug_report_summary(self) -> str:
        mid = self._metric_id or "—"
        return f"[dim]Metric[/] [bold]{mid}[/] [dim]→ get_metric + search_entities[/]"

    def _render_fields(
        self,
        metric_id: str,
        label: str,
        *,
        note: str = "",
        metric: Optional[Metric] = None,
        taxonomies: Optional[list[MetricTaxonomy]] = None,
    ) -> None:
        self._metric_obj = metric
        self.query_one("#metric-title", Label).update(label or metric_id)
        bits: list[str] = []
        if metric is not None and metric.variants:
            v0 = metric.variants[0]
            if v0.units:
                bits.append(v0.units)
            if v0.source:
                bits.append(v0.source)
            if v0.frequency:
                bits.append(v0.frequency)
        if note:
            bits.append(note.strip())
        self.query_one("#metric-meta", Static).update(
            " · ".join(bits) if bits else "Metric"
        )
        self._render_definition(metric=metric, taxonomies=taxonomies)
        self._refresh_tabs()

    def _render_definition(
        self,
        *,
        metric: Optional[Metric] = None,
        taxonomies: Optional[list[MetricTaxonomy]] = None,
    ) -> None:
        m = metric if metric is not None else self._metric_obj
        desc = (
            m.description.strip()
            if m is not None and m.description
            else "[dim]No description in catalog.[/]"
        )
        try:
            self.query_one("#metric-def-desc", Static).update(desc)
        except Exception:  # noqa: BLE001
            pass

        self._variants = list(m.variants) if m is not None else []
        tax = taxonomies if taxonomies is not None else self._taxonomies

        if self._variants:
            lines = [
                f"· {v.frequency or '?'} · {v.source or '—'} · "
                f"{v.seasonal_adjustment or '—'} · {v.units or '—'} · "
                f"{v.series_count} series"
                for v in self._variants
            ]
            variants_text = "\n".join(lines)
        else:
            variants_text = "[dim]No variants in catalog.[/]"
        try:
            self.query_one("#metric-variants", Static).update(variants_text)
        except Exception:  # noqa: BLE001
            pass

        tax_table = self.query_one("#metric-taxonomies", DataTable)
        tax_table.clear()
        for row in tax:
            tax_table.add_row(
                row.name or row.id,
                str(row.entity_count),
                row.id,
                key=row.id,
            )

    def _refresh_tabs(self) -> None:
        n = len(self._entities)
        extra = "+" if self.has_more else ""
        try:
            def_btn = self.query_one("#metric-tab-definition", ActionButton)
            ent_btn = self.query_one("#metric-tab-entities", ActionButton)
            def_btn.set_label("Definition", shortcut="d")
            ent_btn.set_label(f"Entities ({n}{extra})", shortcut="e")
            def_btn.set_class(self._pane == "definition", "-active")
            ent_btn.set_class(self._pane == "entities", "-active")
        except Exception:  # noqa: BLE001
            pass

    def _focus_pane(self, pane: str) -> None:
        self._pane = pane
        entities = pane == "entities"
        self.query_one("#entities-list", ListView).display = entities
        wrap = self.query_one("#assoc-filter-wrap")
        wrap.set_class(entities, "-visible")
        wrap.display = entities
        self.query_one("#metric-definition").display = not entities
        self._refresh_tabs()
        if entities:
            self.query_one("#entities-list", ListView).focus()
        else:
            try:
                self.query_one("#metric-taxonomies", DataTable).focus()
            except Exception:  # noqa: BLE001
                pass
        self.refresh_footer()

    def action_shell_search(self) -> None:
        """``/`` focuses the in-context filter, not universal search."""
        self.action_focus_filter()

    def action_focus_definition(self) -> None:
        self._focus_pane("definition")

    def action_focus_entities(self) -> None:
        self._focus_pane("entities")

    def action_cycle_pane(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        self._focus_pane(
            "entities" if self._pane == "definition" else "definition"
        )

    @on(ActionButton.Pressed)
    def _on_tab_pressed(self, event: ActionButton.Pressed) -> None:
        if event.button_id == "focus_definition":
            self.action_focus_definition()
        elif event.button_id == "focus_entities":
            self.action_focus_entities()

    def footer_action_keys(self) -> list[str]:
        keys: list[str] = []
        if self._loaded:
            keys.append("[bold]tab[/bold] def/entities")
            if self._pane == "entities":
                keys.append("[bold]/[/bold] filter")
                keys.append("[bold]enter[/bold] chart")
                keys.append("[bold]space[/bold] collect")
            else:
                keys.append("[bold]enter[/bold] inspect row")
        else:
            keys.append("[bold]r[/bold] load")
        keys.append("[bold]a[/bold] workspace")
        return keys

    def coach_flags(self) -> set[str]:
        """State the onboarding coach can check (``loaded``)."""
        return {"loaded"} if self._loaded else set()

    def action_load(self) -> None:
        if not self._metric_id:
            self.notify("No metric in Input Context", severity="warning")
            return
        self._filter_query = ""
        self._taxonomy_filter = None
        self.query_one("#assoc-filter", Input).value = ""
        # ``r`` forces a network refresh; pagination / filter reuse disk cache.
        self.load_metric(reset=True, reset_meta=True, refresh=True)

    def action_focus_filter(self) -> None:
        if not self._loaded:
            self.notify("Press r to load first", severity="warning")
            return
        self._focus_pane("entities")
        self.query_one("#assoc-filter", Input).focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#assoc-filter", Input)
        if self.focused is filt:
            entities = self.query_one("#entities-list", ListView)
            entities.focus()
            # Highlight the top match so space collects it (instead of a warning).
            if entities.index is None and len(entities.children):
                entities.index = 0

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._pane == "definition":
            table = self.query_one("#metric-taxonomies", DataTable)
            table.action_cursor_down()
            return
        lv = self.query_one("#entities-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Input):
            return
        if self._pane == "definition":
            table = self.query_one("#metric-taxonomies", DataTable)
            table.action_cursor_up()
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
        return super().check_action(action, parameters)

    def action_collect_self(self) -> None:
        """Stage the focused metric (this page), not a list associate."""
        if not self._metric_id:
            self.notify("No metric in Input Context", severity="warning")
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
        self.notify(f"Added metric {self._label or self._metric_id}")

    def action_collect(self) -> None:
        """Stage the highlighted entity associate."""
        if isinstance(self.focused, Input):
            return
        if self._pane != "entities":
            return
        entity = self._highlighted_entity() if self._loaded else None
        if entity is None:
            self.notify(
                "Highlight an entity, or press a to add this metric",
                severity="warning",
            )
            return
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

    def action_confirm(self) -> None:
        if isinstance(self.focused, Input):
            self.action_leave_filter()
            return
        if self._pane == "definition":
            self._inspect_definition_row()
            return
        if not self._loaded:
            self.notify("Press r to load entities first", severity="warning")
            return
        entity = self._highlighted_entity()
        if entity is None:
            self.notify("No entity highlighted", severity="warning")
            return
        # Chart-first: Enter on an entity opens a draft query for this metric.
        chart_fn = getattr(self.app, "open_direct_query_chart", None)
        metric_ids = [self._metric_id] if self._metric_id else []
        if callable(chart_fn) and metric_ids:
            chart_fn(metric_ids=metric_ids, entity_ids=[entity.id])
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

    def _inspect_definition_row(self) -> None:
        focused = self.focused
        if not isinstance(focused, DataTable) or focused.id != "metric-taxonomies":
            return
        try:
            row_key = focused.coordinate_to_cell_key(
                focused.cursor_coordinate
            ).row_key
        except Exception:  # noqa: BLE001
            return
        self._open_taxonomy(row_key)

    def _open_taxonomy(self, row_key) -> None:  # noqa: ANN001
        tax_id = str(getattr(row_key, "value", row_key) or "")
        if not tax_id:
            return
        tax = next((t for t in self._taxonomies if t.id == tax_id), None)
        label = (tax.name if tax else None) or tax_id
        self._taxonomy_filter = tax_id
        self._filter_query = ""
        self.query_one("#assoc-filter", Input).value = ""
        self._focus_pane("entities")
        self.notify(f"Entities in {label}")
        self.load_metric(reset=True, reset_meta=False, refresh=False)

    @on(DataTable.RowSelected, "#metric-taxonomies")
    def on_taxonomy_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self._open_taxonomy(event.row_key)

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
        freq = ""
        units = ""
        source = ""
        if self._metric_obj and self._metric_obj.variants:
            v = self._metric_obj.variants[0]
            freq = v.frequency or ""
            units = v.units or ""
            source = v.source or ""
        for ent in entities:
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(ent.id, ent.label or ent.id)
            list_view.append(
                EntityAssocRow(
                    ent,
                    frequency=freq,
                    units=units,
                    source=source,
                )
            )

    def _update_status(self, query: str | None) -> None:
        status = self.query_one("#metric-status", Static)
        extra = "+" if self.has_more else ""
        bits: list[str] = []
        if self._taxonomy_filter:
            tax = next(
                (t for t in self._taxonomies if t.id == self._taxonomy_filter),
                None,
            )
            bits.append(f"taxonomy {tax.name if tax else self._taxonomy_filter}")
        if query:
            bits.append(f"“{query}”")
        qbit = (" · " + " · ".join(bits)) if bits else ""
        if self._entities:
            status.update(
                f"{len(self._entities)}{extra} "
                f"entit{'y' if len(self._entities) == 1 else 'ies'}{qbit}"
            )
        else:
            status.update(f"No entities{qbit}")
        self._refresh_tabs()

    def _with_client_cache(self, refresh: bool, fn, /, *args, **kwargs):
        if refresh and hasattr(self.client, "uncached"):
            with self.client.uncached():
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)

    @work(exclusive=True, group="metric-view")
    async def load_metric(
        self,
        *,
        reset: bool = True,
        reset_meta: bool = False,
        refresh: bool = False,
    ) -> None:
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
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
                        self.client.get_metric,
                        metric_id,
                    ),
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
                        self.client.search_entities,
                        query,
                        metric=metric_id,
                        taxonomy=self._taxonomy_filter,
                        limit=fetch_limit,
                        offset=self.page_offset,
                    ),
                    asyncio.to_thread(
                        self._with_client_cache,
                        refresh,
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
                    self._with_client_cache,
                    refresh,
                    self.client.search_entities,
                    query,
                    metric=metric_id,
                    taxonomy=self._taxonomy_filter,
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
                "a",
                "collect_self",
                "Add this metric to the workspace",
            ),
            ActionBinding(
                "space",
                "collect",
                "Collect highlighted entity into the workspace",
            ),
        ),
        example=":add metric",
    )
)
