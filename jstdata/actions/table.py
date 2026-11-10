"""Table View — latest-value comparison over Input Context.

Idle until an explicit load (``r``). Tab toggles row vs column focus; Enter
drills into the focused entity (row mode) or metric (column mode).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import (
    ResearchContext,
    Workspace,
    focus_replace_context,
)
from ..models import PreviewRow, TimeSeries
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    label_for,
    register,
    stage_ids,
)
from .chart import (
    format_compact,
    last_observation_release_id,
    last_observation_value,
    pick_entity,
)
from .drill import DrillDownModal, DrillOption
from .screen import BaseScreen

NAME_WIDTH = 20
COL_WIDTH = 8  # short headers; full metric name lives in the focus strip

FocusMode = Literal["row", "column"]


@dataclass
class TableRow:
    entity_id: str
    entity_label: str
    values: dict[str, Optional[float]] = field(default_factory=dict)
    series_by_metric: dict[str, str] = field(default_factory=dict)
    release_by_metric: dict[str, str] = field(default_factory=dict)


class EntityTableRow(ListItem):
    """One entity row as discrete cells so a column can be painted."""

    def __init__(
        self,
        row: TableRow,
        metric_ids: list[str],
        *,
        name_width: int,
        col_width: int,
        focus_mode: FocusMode,
        col_index: int,
    ) -> None:
        super().__init__()
        self.row = row
        self.metric_ids = metric_ids
        self.name_width = name_width
        self.col_width = col_width
        self.focus_mode = focus_mode
        self.col_index = col_index

    def compose(self) -> ComposeResult:
        name = (self.row.entity_label or self.row.entity_id)[: self.name_width]
        with Horizontal(classes="table-row"):
            yield Label(f"{name:<{self.name_width}}", classes="cell cell-entity")
            for i, mid in enumerate(self.metric_ids):
                val = self.row.values.get(mid)
                raw = format_compact(val) if val is not None else "—"
                classes = "cell cell-metric"
                if self.focus_mode == "column" and i == self.col_index:
                    classes += " col-focus"
                yield Label(raw.rjust(self.col_width), classes=classes)


class TableScreen(BaseScreen):
    """Latest comparison table. No query until the user loads."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
        Binding("enter", "confirm", "Drill down", priority=True),
        Binding("tab", "cycle_focus", "Row/column focus", priority=True),
        Binding(
            "shift+tab",
            "cycle_focus_reverse",
            "Focus reverse",
            show=False,
            priority=True,
        ),
        Binding("j", "cursor_down", "Down / next metric", show=False),
        Binding("k", "cursor_up", "Up / prev metric", show=False),
        Binding("h", "cursor_left", "Prev metric", show=False),
        Binding("l", "cursor_right", "Next metric", show=False),
        Binding("left", "cursor_left", "Prev metric", show=False),
        Binding("right", "cursor_right", "Next metric", show=False),
        Binding("space", "collect", "Collect into workspace", show=False),
    ]

    DEFAULT_CSS = """
    #table-body {
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
    #table-meta {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #table-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #focus-strip {
        color: #e0e0e0;
        padding: 0 1;
        height: 1;
        background: #111;
        border-left: tall #4ade80;
    }
    #col-header-row {
        height: 1;
        background: #1a1a1a;
        border-bottom: solid #333;
        padding: 0 0;
        width: 100%;
    }
    .table-row {
        height: 1;
        width: 100%;
        layout: horizontal;
    }
    .cell {
        height: 1;
        padding: 0 1;
        color: #e0e0e0;
    }
    .cell-entity {
        width: 22;
        color: #c0c0c0;
    }
    .cell-metric {
        width: 10;
        text-align: right;
        color: #a3a3a3;
    }
    .cell-head {
        color: #888;
        text-style: bold;
    }
    .col-focus {
        background: #4ade80;
        color: #0a0a0a;
        text-style: bold;
    }
    .cell-head.col-focus {
        background: #4ade80;
        color: #0a0a0a;
        text-style: bold;
    }
    #table-list {
        height: 1fr;
        background: #0f0f0f;
    }
    ListView > ListItem,
    ListView > ListItem:hover {
        background: transparent;
        color: #e0e0e0;
        padding: 0;
    }
    /* Row mode: whole-row highlight. Column mode: mute it so the green column wins. */
    ListView > ListItem.--highlight {
        background: #1a3a2a;
    }
    TableScreen.-focus-column ListView > ListItem.--highlight {
        background: #121212;
    }
    TableScreen.-focus-column ListView > ListItem.--highlight .cell-entity {
        color: #888;
    }
    TableScreen.-focus-column #focus-strip {
        background: #163324;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        input_context: Optional[ResearchContext] = None,
        sort_by: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self.sort_by = sort_by if sort_by in (None, "id", "value") else None
        self._rows: list[TableRow] = []
        self._metric_ids: list[str] = []
        self._loaded = False
        self._parent_overrides: Optional[ResearchContext] = None
        self._focus_mode: FocusMode = "row"
        self._col_index: int = 0
        # (metric_id, entity_id) → PreviewRow from cheap /preview (cached).
        self._preview: dict[tuple[str, str], PreviewRow] = {}
        self._preview_loaded = False
        self._preview_error: Optional[str] = None

    def _resolved_input_context(self) -> ResearchContext:
        # Do not name this ``_context`` — Textual's MessagePump uses that.
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
        with Vertical(id="table-body"):
            with Vertical(classes="pane-container"):
                yield Label("TABLE // LATEST", classes="pane-header", id="table-header")
                yield Static("", id="table-meta")
                yield Static(
                    "Idle — press [bold]r[/bold] to load",
                    id="table-status",
                    markup=True,
                )
                yield Static("", id="focus-strip", markup=True)
                yield Horizontal(id="col-header-row")
                yield ListView(id="table-list")

    def on_mount(self) -> None:
        super().on_mount()
        action = None
        inv = getattr(self.app, "investigation", None)
        active = getattr(self.app, "active", None)
        if inv is not None and active is not None:
            try:
                action = inv.get_action(active.action)
            except Exception:
                action = None
        if action is not None:
            self._parent_overrides = action.context
        self._render_idle_chrome()
        self.query_one("#table-list", ListView).focus()
        # Cheap coverage only — never auto-run /query.
        self.load_preview()

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold]r[/bold] load values"]
        if self._loaded:
            keys.append("[bold]tab[/bold] row/col")
            keys.append("[bold]enter[/bold] drill")
            keys.append("[bold]space[/bold] collect")
        return keys

    def _coverage_summary(self, ctx: ResearchContext) -> str:
        if self._preview_error:
            return f"preview error: {self._preview_error}"
        if not self._preview_loaded:
            return "coverage pending…"
        if not ctx.metric or not ctx.entity:
            return "coverage n/a (need metric + entity)"
        total = len(ctx.metric) * len(ctx.entity)
        hit = len(self._preview)
        return f"coverage {hit}/{total} pairs have series"

    def _render_idle_chrome(self) -> None:
        ctx = self._resolved_input_context()
        bits = []
        if ctx.entity:
            bits.append(f"{len(ctx.entity)} ent")
        if ctx.metric:
            bits.append(f"{len(ctx.metric)} met")
        if ctx.series:
            bits.append(f"{len(ctx.series)} ser")
        if ctx.taxonomy:
            bits.append(f"tax {ctx.taxonomy}")
        self.query_one("#table-meta", Static).update(
            " · ".join(bits) if bits else "Input Context empty"
        )
        if not self._loaded:
            cover = self._coverage_summary(ctx)
            self.query_one("#table-status", Static).update(
                f"Idle — press [bold]r[/bold] to load values · {cover}",
            )
            self.query_one("#focus-strip", Static).update("")
            self._clear_header_row()

    def action_load(self) -> None:
        self.load_table()

    @work(exclusive=True, group="table-preview")
    async def load_preview(self) -> None:
        """Hydrate metric×entity coverage via /preview (no /query)."""
        ctx = self._resolved_input_context()
        if not ctx.metric or not ctx.entity:
            self._preview = {}
            self._preview_loaded = True
            self._preview_error = None
            if not self._loaded:
                self._render_idle_chrome()
            return
        try:
            rows = await asyncio.to_thread(
                self.client.preview,
                metric=list(ctx.metric),
                entity=list(ctx.entity),
                limit=min(50, max(1, len(ctx.metric) * len(ctx.entity))),
                offset=0,
            )
        except Exception as exc:
            self._preview = {}
            self._preview_loaded = True
            self._preview_error = str(exc)
            if not self._loaded:
                self._render_idle_chrome()
            return
        self._preview = {(r.metric_id, r.entity_id): r for r in rows}
        self._preview_loaded = True
        self._preview_error = None
        if not self._loaded:
            self._render_idle_chrome()

    @work(exclusive=True, group="table")
    async def load_table(self) -> None:
        ctx = self._resolved_input_context()
        status = self.query_one("#table-status", Static)
        if ctx.is_empty() and not ctx.taxonomy:
            status.update("Nothing to query — add metrics/entities via :search")
            return
        status.update("Loading values…")
        kwargs: dict = {"tail": 1}
        if ctx.metric:
            kwargs["metric"] = list(ctx.metric)
        if ctx.entity:
            kwargs["entity"] = list(ctx.entity)
        if ctx.series:
            kwargs["series"] = list(ctx.series)
        if ctx.taxonomy:
            kwargs["taxonomy"] = ctx.taxonomy
        if self.sort_by:
            kwargs["sort_by"] = self.sort_by
        try:
            series_list = await asyncio.to_thread(self.client.query, **kwargs)
        except Exception as exc:
            status.update(f"Error: {exc}")
            return
        self._rows, self._metric_ids = self._build_rows(series_list, ctx)
        self._loaded = True
        self._col_index = 0
        self._focus_mode = "row"
        self._rebuild_list()
        self._refresh_focus_chrome()
        cover = self._coverage_summary(ctx)
        status.update(
            f"{len(self._rows)} rows · {len(self._metric_ids)} metrics · {cover}"
        )
        self.refresh_footer()

    def _build_rows(
        self, series_list: list[TimeSeries], ctx: ResearchContext
    ) -> tuple[list[TableRow], list[str]]:
        labels = self._labels()
        metric_order = list(ctx.metric) if ctx.metric else []
        seen_metrics: list[str] = list(metric_order)
        by_entity: dict[str, TableRow] = {}

        prefer = set(ctx.entity) if ctx.entity else None
        for ts in series_list:
            mid = ts.series.metric_id or ""
            if mid and mid not in seen_metrics:
                seen_metrics.append(mid)
            entity = pick_entity(ts.series.entities, prefer)
            if entity is None:
                continue
            eid = entity.id
            if eid not in by_entity:
                by_entity[eid] = TableRow(
                    entity_id=eid,
                    entity_label=labels.get(eid) or entity.label or eid,
                )
            row = by_entity[eid]
            if mid:
                row.values[mid] = last_observation_value(ts)
                row.series_by_metric[mid] = ts.series.id
                rid = last_observation_release_id(ts)
                if rid:
                    row.release_by_metric[mid] = rid
            if hasattr(self.app, "remember_label"):
                self.app.remember_label(eid, row.entity_label)

        if ctx.entity:
            ordered = [by_entity[e] for e in ctx.entity if e in by_entity]
            for eid, row in by_entity.items():
                if eid not in ctx.entity:
                    ordered.append(row)
        else:
            ordered = list(by_entity.values())

        if self.sort_by == "value" and seen_metrics:
            key_metric = seen_metrics[0]

            def sort_key(r: TableRow) -> float:
                v = r.values.get(key_metric)
                return float("-inf") if v is None else v

            ordered.sort(key=sort_key, reverse=True)

        return ordered, seen_metrics

    def _active_metric_id(self) -> Optional[str]:
        if not self._metric_ids:
            return None
        if self._col_index < 0 or self._col_index >= len(self._metric_ids):
            return None
        return self._metric_ids[self._col_index]

    def _short_metric_header(self, metric_id: str) -> str:
        return metric_id[:COL_WIDTH].rjust(COL_WIDTH)

    def _clear_header_row(self) -> None:
        header = self.query_one("#col-header-row", Horizontal)
        header.remove_children()

    def _rebuild_header_row(self) -> None:
        header = self.query_one("#col-header-row", Horizontal)
        header.remove_children()
        header.mount(
            Label(f"{'ENTITY':<{NAME_WIDTH}}", classes="cell cell-entity cell-head")
        )
        for i, mid in enumerate(self._metric_ids):
            classes = "cell cell-metric cell-head"
            if self._focus_mode == "column" and i == self._col_index:
                classes += " col-focus"
            header.mount(
                Label(self._short_metric_header(mid), classes=classes)
            )

    def _apply_focus_mode_class(self) -> None:
        self.set_class(self._focus_mode == "column", "-focus-column")
        self.set_class(self._focus_mode == "row", "-focus-row")

    def _refresh_focus_chrome(self) -> None:
        if not self._loaded:
            return
        self._apply_focus_mode_class()
        n = len(self._rows)
        m = len(self._metric_ids)
        mode = self._focus_mode.upper()
        self.query_one("#table-header", Label).update(f"TABLE // {mode}")
        self.query_one("#table-status", Static).update(
            f"{n} rows · {m} metrics · focus={self._focus_mode} · tail=1"
        )
        strip = self.query_one("#focus-strip", Static)
        if self._focus_mode == "row":
            row = self._highlighted_row()
            if row is None:
                strip.update("[bold]row[/bold]  —")
            else:
                strip.update(
                    f"[bold]row[/bold]  {row.entity_label or row.entity_id}  "
                    f"[dim]{row.entity_id}[/dim]"
                )
        else:
            mid = self._active_metric_id()
            if mid is None:
                strip.update("[bold]column[/bold]  —")
            else:
                label = label_for(self._labels(), mid)
                if label and label != mid:
                    strip.update(
                        f"[bold]column[/bold]  {mid}  [dim]{label}[/dim]"
                    )
                else:
                    strip.update(f"[bold]column[/bold]  {mid}")
        self._rebuild_header_row()

    def _rebuild_list(self) -> None:
        list_view = self.query_one("#table-list", ListView)
        prev_index = list_view.index
        list_view.clear()
        for row in self._rows:
            list_view.append(
                EntityTableRow(
                    row,
                    self._metric_ids,
                    name_width=NAME_WIDTH,
                    col_width=COL_WIDTH,
                    focus_mode=self._focus_mode,
                    col_index=self._col_index,
                )
            )
        if self._rows and prev_index is not None:
            list_view.index = min(prev_index, len(self._rows) - 1)
        self._refresh_focus_chrome()

    def _highlighted_row(self) -> Optional[TableRow]:
        child = self.query_one("#table-list", ListView).highlighted_child
        if isinstance(child, EntityTableRow):
            return child.row
        return None

    def _entity_drill_option(self, row: TableRow) -> DrillOption:
        return DrillOption(
            title=f"Entity · {row.entity_label or row.entity_id}",
            detail="Metadata and relationships",
            action_type="entity",
            context=focus_replace_context(
                self._parent_overrides, entity=[row.entity_id]
            ),
            description=f"Entity {row.entity_label or row.entity_id}",
        )

    def _metric_drill_option(self, metric_id: str) -> DrillOption:
        labels = self._labels()
        mlabel = labels.get(metric_id) or metric_id
        return DrillOption(
            title=f"Metric · {mlabel}",
            detail=metric_id,
            action_type="metric",
            context=focus_replace_context(
                self._parent_overrides, metric=[metric_id]
            ),
            description=f"Metric {mlabel}",
        )

    def _release_drill_option(
        self, row: TableRow, metric_id: str, release_id: str
    ) -> DrillOption:
        labels = self._labels()
        mlabel = labels.get(metric_id) or metric_id
        value = row.values.get(metric_id)
        detail = f"{row.entity_label or row.entity_id} · {mlabel}"
        if value is not None:
            detail = f"{detail} · {format_compact(value)}"
        return DrillOption(
            title=f"Release · {release_id}",
            detail=detail,
            action_type="release",
            context=focus_replace_context(
                self._parent_overrides,
                entity=[row.entity_id],
                metric=[metric_id],
                release=[release_id],
            ),
            description=f"Release {release_id}",
        )

    def _drill_options(self) -> list[DrillOption]:
        """Entity / metric / release options for the focused cell."""
        row = self._highlighted_row()
        mid = self._active_metric_id()
        options: list[DrillOption] = []
        if self._focus_mode == "column":
            if mid is not None:
                options.append(self._metric_drill_option(mid))
            if row is not None and mid is not None:
                rid = row.release_by_metric.get(mid)
                if rid:
                    options.append(self._release_drill_option(row, mid, rid))
            return options
        if row is None:
            return []
        options.append(self._entity_drill_option(row))
        if mid is not None:
            options.append(self._metric_drill_option(mid))
            rid = row.release_by_metric.get(mid)
            if rid:
                options.append(self._release_drill_option(row, mid, rid))
        return options

    def action_collect(self) -> None:
        row = self._highlighted_row()
        mid = self._active_metric_id()
        labels: dict[str, str] = {}
        entity_id = None
        metric_id = None
        if row is not None:
            entity_id = row.entity_id
            labels[entity_id] = row.entity_label or entity_id
        if mid is not None:
            metric_id = mid
            labels[mid] = label_for(self._labels(), mid)
        if self._focus_mode == "column":
            if metric_id is None:
                self.notify("No metric column to collect", severity="warning")
                return
        elif entity_id is None:
            self.notify("No row to collect", severity="warning")
            return
        added = stage_ids(
            self.workspace,
            self.app,
            metric=metric_id,
            entity=entity_id,
            labels=labels,
        )
        if not added:
            self.notify("Already in workspace", severity="warning")
            return
        self.notify("Collected " + ", ".join(added))

    def action_confirm(self) -> None:
        if not self._loaded:
            self.notify("Press r to load before drilling down", severity="warning")
            return
        options = self._drill_options()
        if not options:
            if self._focus_mode == "column":
                self.notify("No metric column to drill into", severity="warning")
            else:
                self.notify("No row highlighted", severity="warning")
            return
        if len(options) == 1:
            self._open_drill_option(options[0])
            return
        heading = (
            "DRILL · column"
            if self._focus_mode == "column"
            else "DRILL · row"
        )
        self.app.push_screen(
            DrillDownModal(options, heading=heading),
            self._on_drill_chosen,
        )

    def _on_drill_chosen(self, option: DrillOption | None) -> None:
        if option is None:
            return
        self.app.call_later(self._open_drill_option, option)

    def _open_drill_option(self, option: DrillOption) -> None:
        open_fn = getattr(self.app, "open_child_view", None)
        if open_fn is None:
            self.notify("Host cannot open child views", severity="error")
            return
        open_fn(
            option.action_type,
            context=option.context,
            args=dict(option.args),
            description=option.description,
        )

    def action_cycle_focus(self) -> None:
        if not self._loaded:
            return
        self._focus_mode = "column" if self._focus_mode == "row" else "row"
        self._rebuild_list()
        self.refresh_footer()

    def action_cycle_focus_reverse(self) -> None:
        self.action_cycle_focus()

    def _move_column(self, delta: int) -> None:
        if not self._metric_ids:
            return
        self._col_index = (self._col_index + delta) % len(self._metric_ids)
        self._rebuild_list()

    def action_cursor_down(self) -> None:
        if self._focus_mode == "column":
            self._move_column(+1)
            return
        lv = self.query_one("#table-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()
        self._refresh_focus_chrome()

    def action_cursor_up(self) -> None:
        if self._focus_mode == "column":
            self._move_column(-1)
            return
        lv = self.query_one("#table-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()
        self._refresh_focus_chrome()

    def action_cursor_left(self) -> None:
        if not self._loaded or not self._metric_ids:
            return
        if self._focus_mode != "column":
            self._focus_mode = "column"
        self._move_column(-1)
        self.refresh_footer()

    def action_cursor_right(self) -> None:
        if not self._loaded or not self._metric_ids:
            return
        if self._focus_mode != "column":
            self._focus_mode = "column"
        self._move_column(+1)
        self.refresh_footer()

    @on(ListView.Highlighted, "#table-list")
    def on_row_highlighted(self, event: ListView.Highlighted) -> None:
        if self._loaded and self._focus_mode == "row":
            self._refresh_focus_chrome()


def create_table_screen(
    client,
    workspace,
    input_context=None,
    sort_by=None,
    **kwargs,
):
    return TableScreen(
        client,
        workspace,
        input_context=input_context,
        sort_by=sort_by,
    )


TABLE = register(
    ActionSpec(
        id="table",
        name="Table",
        description=(
            "Latest-value comparison over Input Context. Cheap /preview "
            "coverage on open; /query only on r. Enter drills to entity, "
            "metric, or release."
        ),
        create_screen=create_table_screen,
        arguments=(
            ActionArgument(
                name="sort_by",
                type="string",
                description="Query sort: id (default) or value (rank-like)",
                choices=("id", "value"),
            ),
        ),
        bindings=(
            ActionBinding("r", "load", "Load /query values for Input Context"),
            ActionBinding("tab", "cycle_focus", "Toggle row vs column focus"),
            ActionBinding(
                "h / l · ←/→ · j/k",
                "columns",
                "Move metric column (column mode; j/k also move columns)",
            ),
            ActionBinding(
                "enter",
                "confirm",
                "Drill into entity, metric, or release for the focused cell",
            ),
            ActionBinding(
                "space",
                "collect",
                "Collect focused entity and metric into the workspace",
            ),
        ),
        example="jst action add table --args '{\"sort_by\":\"value\"}'",
    )
)
