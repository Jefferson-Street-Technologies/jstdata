"""Rank action — leaderboard for one workspace metric across a metric population."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..models import TimeSeries
from ..investigation.model import Workspace
from .base import ActionArgument, ActionBinding, ActionSpec, label_for, register
from .chart import (
    VALUE_WIDTH,
    format_rank_line,
    last_observation_timestamp,
    last_observation_value,
    pick_entity,
    shared_pane_max,
)

PAGE_SIZE = 50
RANK_DEBOUNCE_S = 0.4
TAXONOMY_PAGE = 20
TAXONOMY_ID_CAP = 2000
NAME_WIDTH = 18


@dataclass
class RankEntry:
    entity_id: str
    entity_label: str
    series_id: str
    value: float
    frequency: str
    units: str
    as_of: Optional[datetime]


class MetricRow(ListItem):
    def __init__(self, metric_id: str, caption: str) -> None:
        super().__init__()
        self.metric_id = metric_id
        self.caption = caption

    def compose(self) -> ComposeResult:
        yield Label(self.caption)


class RankRow(ListItem):
    def __init__(self, entry: RankEntry, rank: int, pane_max: float, bar_width: int) -> None:
        super().__init__()
        self.entry = entry
        self.rank = rank
        self.pane_max = pane_max
        self.bar_width = bar_width

    def compose(self) -> ComposeResult:
        yield Label(
            format_rank_line(
                self.rank,
                self.entry.entity_label,
                self.entry.value,
                self.pane_max,
                self.bar_width,
                name_width=NAME_WIDTH,
            )
        )


class RankScreen(Screen):
    """Workspace metrics on the left; value-sorted leaderboard on the right."""

    BINDINGS = [
        Binding("slash", "focus_filter", "Filter ranking", key_display="/"),
        Binding("escape", "leave_filter", "Leave filter", show=False),
        Binding("tab", "cycle_panes", "Switch metrics / rank", priority=True),
        Binding(
            "shift+tab",
            "cycle_panes",
            "Switch panes reverse",
            show=False,
            priority=True,
        ),
        Binding("enter", "confirm", "Add entity / open ranking", priority=True),
        Binding("shift+enter", "add_entity", "Add entity to workspace", show=False, priority=True),
        Binding("j", "cursor_down", "Move highlight down", show=False),
        Binding("k", "cursor_up", "Move highlight up", show=False),
    ]

    DEFAULT_CSS = """
    #rank-body { height: 1fr; }

    .pane-container {
        border: solid #222;
        background: #0f0f0f;
    }
    #metrics-pane { width: 36%; }
    #board-pane { width: 1fr; }

    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }

    #metrics-list, #rank-list {
        height: 1fr;
        background: #0f0f0f;
    }

    ListView > ListItem,
    MetricRow,
    RankRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        width: 100%;
        padding: 0 1;
        overflow: hidden;
    }

    MetricRow:focus,
    MetricRow.-highlighted,
    RankRow:focus,
    RankRow.-highlighted {
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

    #rank-filter {
        height: 3;
        background: transparent;
        border: none;
        border-bottom: solid #333;
        padding: 0 1;
    }

    #board-meta {
        height: 1;
        color: #888;
        padding: 0 1;
    }

    #board-status {
        height: 1;
        color: #888;
        padding: 0 1;
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
        taxonomy: Optional[str] = None,
        workspace_entities: bool = True,
    ):
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.taxonomy = (taxonomy or "").strip() or None
        self.workspace_entities = bool(workspace_entities)
        self.filter_query = ""
        self.page_offset = 0
        self.has_more = False
        self.loading_more = False
        self.rank_task: asyncio.Task[None] | None = None
        self._metric_snapshot: tuple[str, ...] = ()
        self._active_metric_id: Optional[str] = None
        self._board: list[RankEntry] = []
        self._taxonomy_ids: set[str] | None = None
        self._prefer_entity_ids: set[str] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="rank-body"):
            with Vertical(classes="pane-container", id="metrics-pane"):
                yield Label("METRICS // WORKSPACE", classes="pane-header")
                yield ListView(id="metrics-list")
                yield Static(
                    "No metrics in workspace.\n\n"
                    "Press [bold]f[/bold] to find metrics and add them.\n"
                    "Then highlight a metric — the ranking fills on the right.\n"
                    "Tab to the ranking, Enter to add an entity.",
                    id="empty-prompt",
                    markup=True,
                )
            with Vertical(classes="pane-container", id="board-pane"):
                yield Label("RANK // —", classes="pane-header", id="board-header")
                yield Label("", id="board-meta")
                yield Input(
                    placeholder="/ filter this page by name…",
                    id="rank-filter",
                )
                yield ListView(id="rank-list")
                yield Label("", id="board-status")
        with Horizontal(id="cmd-bar"):
            yield Label("", id="help-hint")

    def on_mount(self) -> None:
        self._refresh_help_hint()
        if self.taxonomy:
            self.load_taxonomy_ids()
        else:
            self._prefer_entity_ids = self._entity_preference_ids()
        self._sync_metrics(force=True)
        self.query_one("#metrics-list", ListView).focus()

    def on_screen_resume(self) -> None:
        self._sync_metrics()

    def _labels(self) -> dict[str, str]:
        return getattr(self.app, "labels", {})

    def _refresh_help_hint(self) -> None:
        n = getattr(self.app, "action_count", 1)
        i = getattr(self.app, "action_index", 0) + 1
        tax = self.taxonomy or "all"
        scope = (
            "workspace entities"
            if self._using_workspace_entities()
            else f"taxonomy {tax}"
        )
        parts = [
            f"rank {i}/{n}",
            scope,
            "[bold]/[/bold] filter",
            "[bold]enter[/bold] open rank / add",
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

    def _using_workspace_entities(self) -> bool:
        """True when ranking is scoped to non-empty workspace.entity."""
        return bool(self.workspace_entities and self.workspace.entity)

    def _scoped_entity_ids(self) -> list[str] | None:
        """Entity ids for /query, or None to use taxonomy/all population."""
        if not self._using_workspace_entities():
            return None
        return list(self.workspace.entity)

    def _entity_preference_ids(self) -> set[str] | None:
        """Ids to prefer when labeling multi-entity series."""
        prefer = self._taxonomy_ids
        if self._using_workspace_entities():
            scoped_ids = set(self.workspace.entity)
            if prefer is not None:
                return prefer & scoped_ids or scoped_ids
            return scoped_ids
        return prefer

    def _sync_metrics(self, force: bool = False) -> None:
        snapshot = tuple(self.workspace.metric)
        if not force and snapshot == self._metric_snapshot:
            return
        self._metric_snapshot = snapshot
        empty = self.query_one("#empty-prompt")
        roster = self.query_one("#metrics-list", ListView)
        roster.clear()
        if not snapshot:
            empty.display = True
            roster.display = False
            self._clear_board("Add metrics to rank a population.")
            return
        empty.display = False
        roster.display = True
        labels = self._labels()
        for mid in snapshot:
            name = label_for(labels, mid)
            roster.append(MetricRow(mid, f"  {name}  [{mid}]"))
        # Defer highlight + first fetch until rows are mounted.
        self.call_after_refresh(self._select_initial_metric)

    def _select_initial_metric(self) -> None:
        roster = self.query_one("#metrics-list", ListView)
        if not len(roster):
            return
        if roster.index is None:
            roster.index = 0
        child = roster.highlighted_child
        if isinstance(child, MetricRow):
            self._active_metric_id = child.metric_id
            self.load_rank(reset=True)

    def _clear_board(self, status: str) -> None:
        self._board = []
        self._active_metric_id = None
        self.has_more = False
        self.page_offset = 0
        self.query_one("#rank-list", ListView).clear()
        self.query_one("#board-header").update("RANK // —")
        self.query_one("#board-meta").update("")
        self.query_one("#board-status").update(status)

    def _bar_width(self) -> int:
        # pane ≈ body width minus metrics pane; leave room for rank/name/value.
        width = self.size.width or 80
        usable = max(24, int(width * 0.64) - NAME_WIDTH - VALUE_WIDTH - 12)
        return usable

    def _filtered_board(self) -> list[RankEntry]:
        q = self.filter_query.strip().lower()
        if not q:
            return list(self._board)
        out: list[RankEntry] = []
        for entry in self._board:
            hay = f"{entry.entity_label} {entry.entity_id}".lower()
            if q in hay:
                out.append(entry)
        return out

    def _rebuild_rank_list(self) -> None:
        list_view = self.query_one("#rank-list", ListView)
        list_view.clear()
        rows = self._filtered_board()
        if not rows:
            return
        pane_max = shared_pane_max(e.value for e in rows)
        bar_width = self._bar_width()
        # Rank numbers follow absolute board order, not the filtered subset.
        index_by_id = {
            (e.entity_id, e.series_id): i + 1 for i, e in enumerate(self._board)
        }
        for entry in rows:
            rank = index_by_id.get((entry.entity_id, entry.series_id), 0)
            list_view.append(RankRow(entry, rank, pane_max, bar_width))
        # clear() leaves index=None; without this, Enter's select_cursor is a no-op
        # until the user moves with j/k.
        list_view.index = 0

    def _entries_from_series(self, series_list: list[TimeSeries]) -> list[RankEntry]:
        entries: list[RankEntry] = []
        for ts in series_list:
            value = last_observation_value(ts)
            if value is None:
                continue
            entity = pick_entity(ts.series.entities, self._prefer_entity_ids)
            if entity is None:
                continue
            entries.append(
                RankEntry(
                    entity_id=entity.id,
                    entity_label=entity.label or entity.id,
                    series_id=ts.series.id,
                    value=value,
                    frequency=ts.series.frequency or "",
                    units=ts.series.units or "",
                    as_of=last_observation_timestamp(ts),
                )
            )
        return entries

    def _scope_label(self) -> str:
        if self._using_workspace_entities():
            return f"workspace ({len(self.workspace.entity)})"
        return self.taxonomy or "all"

    @work(exclusive=True, group="taxonomy")
    async def load_taxonomy_ids(self) -> None:
        if not self.taxonomy:
            self._taxonomy_ids = None
            self._prefer_entity_ids = self._entity_preference_ids()
            return
        ids: set[str] = set()
        offset = 0
        try:
            while offset < TAXONOMY_ID_CAP:
                page = await asyncio.to_thread(
                    self.client.get_taxonomy_entities,
                    self.taxonomy,
                    TAXONOMY_PAGE,
                    offset,
                )
                if not page:
                    break
                ids.update(e.id for e in page)
                if len(page) < TAXONOMY_PAGE:
                    break
                offset += TAXONOMY_PAGE
                if len(ids) >= TAXONOMY_ID_CAP:
                    break
        except Exception:
            self._taxonomy_ids = None
            self._prefer_entity_ids = self._entity_preference_ids()
            return
        self._taxonomy_ids = ids
        self._prefer_entity_ids = self._entity_preference_ids()

    @work(exclusive=True, group="rank")
    async def load_rank(self, reset: bool = False) -> None:
        metric_id = self._active_metric_id
        if not metric_id:
            self.loading_more = False
            return
        if reset:
            self.page_offset = 0
            self.has_more = False
            self._board = []
        status = self.query_one("#board-status")
        labels = self._labels()
        name = label_for(labels, metric_id)
        self.query_one("#board-header").update(f"RANK // {name}")
        scope = self._scope_label()
        entity_ids = self._scoped_entity_ids()

        status.update("Loading ranking…")
        try:
            series_list: list[TimeSeries] = await asyncio.to_thread(
                self.client.query,
                metric=metric_id,
                entity=entity_ids,
                taxonomy=self.taxonomy,
                tail=1,
                sort_by="value",
                limit=PAGE_SIZE,
                offset=self.page_offset,
            )
        except Exception as e:
            status.update(f"Error: {e}")
            self.loading_more = False
            return
        if metric_id != self._active_metric_id:
            self.loading_more = False
            return
        if reset:
            self._board = []
        page = self._entries_from_series(series_list)
        self._board.extend(page)
        self.has_more = len(series_list) == PAGE_SIZE
        self._rebuild_rank_list()

        meta_bits: list[str] = []
        if page:
            freqs = {e.frequency for e in self._board if e.frequency}
            units = next((e.units for e in self._board if e.units), "")
            as_ofs = [e.as_of for e in self._board if e.as_of is not None]
            if len(freqs) == 1:
                meta_bits.append(next(iter(freqs)))
            elif freqs:
                meta_bits.append("mixed freq")
            if units:
                meta_bits.append(units)
            if as_ofs:
                years = {t.year for t in as_ofs}
                if len(years) == 1:
                    meta_bits.append(f"as of {next(iter(years))}")
                else:
                    meta_bits.append(f"as of {min(years)}–{max(years)}")
        self.query_one("#board-meta").update(" · ".join(meta_bits))

        shown = len(self._filtered_board())
        total = len(self._board)
        extra = "+" if self.has_more else ""
        q = self.filter_query.strip()
        qbit = f" · filter “{q}”" if q else ""
        if reset and not series_list:
            if entity_ids is not None:
                scope_bit = " among workspace entities"
            elif self.taxonomy:
                scope_bit = f" in taxonomy “{self.taxonomy}”"
            else:
                scope_bit = ""
            status.update(
                f"No series for “{metric_id}”{scope_bit}. "
                "Try another metric, or check the metric id."
            )
        elif reset and series_list and not page:
            status.update(
                f"Got {len(series_list)} series but no entities to show · {scope}"
            )
        else:
            status.update(f"{shown}/{total}{extra} · desc · {scope}{qbit}")
        self.loading_more = False

    def action_focus_filter(self) -> None:
        self.query_one("#rank-filter", Input).focus()

    def action_leave_filter(self) -> None:
        filt = self.query_one("#rank-filter", Input)
        if self.focused is filt:
            self.query_one("#rank-list", ListView).focus()

    def action_cycle_panes(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one("#rank-list", ListView).focus()
            return
        if self.focused is self.query_one("#metrics-list"):
            self.query_one("#rank-list", ListView).focus()
        else:
            self.query_one("#metrics-list", ListView).focus()

    def action_cursor_down(self) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            return
        if focused and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.focused
        if isinstance(focused, Input):
            return
        if focused and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()

    def _highlighted_rank(self) -> Optional[RankEntry]:
        child = self.query_one("#rank-list", ListView).highlighted_child
        if isinstance(child, RankRow):
            return child.entry
        return None

    def _rank_list_focused(self) -> bool:
        focused = self.focused
        if focused is self.query_one("#rank-list"):
            return True
        node = focused
        while node is not None:
            if getattr(node, "id", None) == "rank-list":
                return True
            node = getattr(node, "parent", None)
        return False

    def _add_entity(self, entry: RankEntry) -> None:
        if not self.workspace.add_entity(entry.entity_id):
            self.notify(f"{entry.entity_id} is already in workspace", severity="warning")
            return
        if hasattr(self.app, "remember_label"):
            self.app.remember_label(entry.entity_id, entry.entity_label)
        self.notify(f"Added {entry.entity_id} to workspace")

    def action_confirm(self) -> None:
        """Enter: on the board, add entity; on metrics, jump to the board."""
        if isinstance(self.focused, Input):
            return
        if self._rank_list_focused():
            self.action_add_entity()
            return
        # Metrics list: ranking loads on highlight — just move focus over.
        self.query_one("#rank-list", ListView).focus()

    def action_add_entity(self) -> None:
        if isinstance(self.focused, Input):
            return
        list_view = self.query_one("#rank-list", ListView)
        entry = self._highlighted_rank()
        if entry is None and len(list_view):
            list_view.index = 0
            entry = self._highlighted_rank()
        if entry is None:
            if not self._board:
                self.notify(
                    "Ranking is empty — pick a metric that has series "
                    "(right pane shows why)",
                    severity="warning",
                )
            else:
                self.notify("No entity selected on the ranking", severity="warning")
            return
        self._add_entity(entry)

    @on(ListView.Selected, "#rank-list")
    def on_rank_selected(self, event: ListView.Selected) -> None:
        """Enter on the leaderboard adds the highlighted entity."""
        if isinstance(event.item, RankRow):
            self._add_entity(event.item.entry)

    @on(ListView.Selected, "#metrics-list")
    def on_metric_selected(self, event: ListView.Selected) -> None:
        """Enter on a metric focuses the ranking for that metric."""
        if isinstance(event.item, MetricRow):
            self._active_metric_id = event.item.metric_id
            self.load_rank(reset=True)
        self.query_one("#rank-list", ListView).focus()

    @on(Input.Changed, "#rank-filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_query = event.value
        self._rebuild_rank_list()
        shown = len(self._filtered_board())
        total = len(self._board)
        extra = "+" if self.has_more else ""
        scope = self._scope_label()
        q = self.filter_query.strip()
        qbit = f" · filter “{q}”" if q else ""
        self.query_one("#board-status").update(
            f"{shown}/{total}{extra} · desc · {scope}{qbit}"
        )

    @on(Input.Submitted, "#rank-filter")
    def on_filter_submit(self) -> None:
        self.query_one("#rank-list", ListView).focus()

    @on(ListView.Highlighted, "#metrics-list")
    def on_metric_highlighted(self, event: ListView.Highlighted) -> None:
        if not isinstance(event.item, MetricRow):
            return
        metric_id = event.item.metric_id
        if self.rank_task:
            self.rank_task.cancel()
        self.rank_task = asyncio.create_task(self._debounced_rank(metric_id))

    async def _debounced_rank(self, metric_id: str) -> None:
        try:
            await asyncio.sleep(RANK_DEBOUNCE_S)
            self._active_metric_id = metric_id
            self.load_rank(reset=True)
        except asyncio.CancelledError:
            pass

    @on(ListView.Highlighted, "#rank-list")
    def on_rank_highlighted(self, event: ListView.Highlighted) -> None:
        if self.loading_more or not self.has_more:
            return
        if self.filter_query.strip():
            return
        list_view = self.query_one("#rank-list", ListView)
        if event.item is None:
            return
        index = list_view.index
        if index is None or index < len(self._board) - 1:
            return
        self.loading_more = True
        self.page_offset = len(self._board)
        self.load_rank(reset=False)


def create_rank_screen(
    client, workspace, taxonomy=None, workspace_entities=True, **kwargs
):
    return RankScreen(
        client,
        workspace,
        taxonomy=taxonomy,
        workspace_entities=workspace_entities,
    )


RANK = register(
    ActionSpec(
        id="rank",
        name="Rank",
        description=(
            "Rank entities for a workspace metric by latest value "
            "(shared-scale leaderboard)."
        ),
        create_screen=create_rank_screen,
        arguments=(
            ActionArgument(
                name="taxonomy",
                type="string",
                description=(
                    "Restrict ranking to entities in this taxonomy"
                ),
            ),
            ActionArgument(
                name="workspace_entities",
                type="boolean",
                description=(
                    "When true (default), rank only workspace entities if any "
                    "are present; with an empty entity list, ranks the full "
                    "taxonomy/all population. Set false to always ignore "
                    "workspace entities."
                ),
                default=True,
            ),
        ),
        bindings=(
            ActionBinding("/", "focus_filter", "Filter the current ranking page by name"),
            ActionBinding(
                "enter",
                "confirm",
                "On ranking: add entity. On metrics: open the ranking pane.",
            ),
            ActionBinding(
                "shift+enter",
                "add_entity",
                "Add the highlighted ranked entity to the workspace",
            ),
            ActionBinding(
                "tab", "cycle_panes", "Switch focus between metrics and ranking"
            ),
            ActionBinding("j / k", "cursor", "Move highlight in the focused list"),
            ActionBinding("escape", "leave_filter", "Leave the ranking filter"),
        ),
        example=(
            "jst action add rank --args "
            "'{\"taxonomy\":\"country\",\"workspace_entities\":true}'"
        ),
    )
)
