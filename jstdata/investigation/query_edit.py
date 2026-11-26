"""In-pane query editor — refine investigation scope, not API form fields.

Mounted inside QueryScreen as the Edit view (alongside Chart / Table / Sources).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from .api import add_query, persist_investigation
from .model import Investigation, Query
from .naming import label_for

FREQUENCIES: tuple[Optional[str], ...] = (
    None,
    "Daily",
    "Weekly",
    "Monthly",
    "Quarterly",
    "Annual",
    "Intraday",
)

ObsMode = Literal["most_recent", "earliest", "all"]
OBS_MODES: tuple[ObsMode, ...] = ("most_recent", "earliest", "all")
OBS_LABELS = {
    "most_recent": "Most recent",
    "earliest": "Earliest",
    "all": "All available",
}


def _obs_from_query(query: Query) -> tuple[ObsMode, Optional[int]]:
    if query.tail is not None:
        return "most_recent", int(query.tail)
    if query.head is not None:
        return "earliest", int(query.head)
    return "all", None


def _head_tail(mode: ObsMode, count: Optional[int]) -> tuple[Optional[int], Optional[int]]:
    if mode == "all" or count is None:
        return None, None
    if mode == "earliest":
        return int(count), None
    return None, int(count)


@dataclass
class _Snapshot:
    name: str
    metric: tuple[str, ...]
    entity: tuple[str, ...]
    series: tuple[str, ...]
    frequency: Optional[str]
    obs_mode: ObsMode
    obs_count: Optional[int]


class _ResourceRow(ListItem):
    def __init__(
        self,
        resource_id: str,
        label: str,
        *,
        dirty: bool = False,
        add: bool = False,
        kind: str = "",
    ) -> None:
        self.resource_id = resource_id
        self.resource_label = label
        self.is_add = add
        self.kind = kind
        if add:
            text = f"[#4ade80]+ Add {kind}[/]"
        elif dirty:
            text = f"[#fbbf24]{label}[/]  [dim]·[/]"
        else:
            text = label
        super().__init__(Label(text, markup=True))


class _EnumField(Static, can_focus=True):
    """Focusable left/right enum selector."""

    DEFAULT_CSS = """
    _EnumField {
        height: 1;
        width: auto;
        min-width: 18;
        padding: 0 1;
        color: #e0e0e0;
    }
    _EnumField:focus {
        background: #1a3a2a;
        color: #86efac;
        text-style: bold;
    }
    """

    def __init__(
        self,
        options: Sequence[Any],
        *,
        labels: Optional[dict[Any, str]] = None,
        value: Any = None,
        id: Optional[str] = None,
    ) -> None:
        super().__init__("", id=id)
        self._options = list(options)
        self._labels = labels or {}
        if value in self._options:
            self._index = self._options.index(value)
        else:
            self._index = 0

    @property
    def value(self) -> Any:
        return self._options[self._index]

    def set_value(self, value: Any) -> None:
        if value in self._options:
            self._index = self._options.index(value)
        self._paint()

    def on_mount(self) -> None:
        self._paint()

    def _paint(self) -> None:
        raw = self.value
        label = self._labels.get(raw, "Any" if raw is None else str(raw))
        self.update(f"◀ {label} ▶")

    def action_cursor_left(self) -> None:
        self._index = (self._index - 1) % len(self._options)
        self._paint()
        self.post_message(self.Changed())

    def action_cursor_right(self) -> None:
        self._index = (self._index + 1) % len(self._options)
        self._paint()
        self.post_message(self.Changed())

    def on_key(self, event) -> None:  # noqa: ANN001
        if event.key in {"left", "h"}:
            self.action_cursor_left()
            event.stop()
        elif event.key in {"right", "l"}:
            self.action_cursor_right()
            event.stop()

    class Changed(Message):
        """Frequency or observation-mode selector changed."""


class QueryEditor(Vertical):
    """Researcher-facing query composition pane."""

    BINDINGS = [
        Binding("ctrl+s", "apply_update", "Update", show=False),
        Binding("ctrl+n", "save_as_new", "Save as new", show=False),
        Binding("escape", "cancel_edit", "Cancel", show=False),
        Binding("backspace", "remove_resource", "Remove", show=False),
        Binding("enter", "activate", "Select", show=False, priority=True),
        Binding("slash", "add_entity", "Add entity", show=False),
        Binding("m", "add_metric", "Add metric", show=False),
        Binding("s", "add_series", "Add series", show=False),
    ]

    DEFAULT_CSS = """
    QueryEditor {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
        display: none;
    }
    QueryEditor.-visible { display: block; }
    #qe-scroll { height: 1fr; }
    #qe-heading {
        color: #888;
        text-style: bold;
        height: 1;
        margin: 1 0 0 0;
    }
    #qe-name-row { height: 3; margin-bottom: 0; }
    #qe-name-label { width: 12; color: #888; height: 1; }
    #qe-name {
        width: 56;
        max-width: 100%;
    }
    #qe-name.-dirty { border: tall #fbbf24; }
    .qe-section {
        color: #666;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    .qe-list-label { color: #a0a0a0; height: 1; margin-top: 1; }
    .qe-list {
        height: auto;
        max-height: 8;
        min-height: 2;
        background: #0f0f0f;
        border: solid #222;
        padding: 0 1;
        width: 64;
        max-width: 100%;
    }
    .qe-list > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    #qe-scope-row, #qe-obs-row {
        height: 1;
        margin-top: 1;
        width: 64;
    }
    .qe-field-label { width: 14; color: #888; }
    #qe-count {
        width: 10;
        margin-left: 2;
        display: none;
    }
    #qe-count.-show { display: block; }
    #qe-count.-dirty { border: tall #fbbf24; }
    #qe-divider {
        height: 1;
        color: #333;
        margin: 1 0;
        width: 64;
    }
    #qe-coverage {
        height: auto;
        min-height: 1;
        max-height: 12;
        color: #a0a0a0;
        width: 64;
    }
    #qe-coverage.-ok { color: #86efac; }
    #qe-coverage.-warn { color: #fbbf24; }
    #qe-coverage.-err { color: #f87171; }
    #qe-actions {
        height: 3;
        margin-top: 1;
        align: left middle;
    }
    #qe-update { margin-right: 2; }
    #qe-save-new { margin-right: 2; }
    #qe-hints {
        color: #555;
        height: 1;
        margin-top: 0;
    }
    """

    dirty = reactive(False)

    class Applied(Message):
        def __init__(self, query_id: int, mode: str) -> None:
            super().__init__()
            self.query_id = query_id
            self.mode = mode

    class Cancelled(Message):
        pass

    def __init__(
        self,
        client: JSTDataClient,
        *,
        id: str = "query-edit",
    ) -> None:
        super().__init__(id=id)
        self.client = client
        self.edit_query: Optional[Query] = None
        self.investigation: Optional[Investigation] = None
        self.app_dir: Optional[Path] = None
        self.project_id: Optional[str] = None
        self.investigation_id: Optional[str] = None
        self._metric: list[str] = []
        self._entity: list[str] = []
        self._series: list[str] = []
        self._name = ""
        self._frequency: Optional[str] = None
        self._obs_mode: ObsMode = "most_recent"
        self._obs_count: Optional[int] = 20
        self._baseline: Optional[_Snapshot] = None
        self._coverage: Optional[dict[str, Any]] = None
        self._show_matrix = False
        self._labels: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="qe-scroll"):
            yield Static("", id="qe-heading")
            with Horizontal(id="qe-name-row"):
                yield Label("Name", id="qe-name-label")
                yield Input(placeholder="Query name", id="qe-name")
            yield Static("DATA", classes="qe-section")
            yield Static("Entities (0)", id="qe-entity-label", classes="qe-list-label")
            yield ListView(id="qe-entities", classes="qe-list")
            yield Static("Metrics (0)", id="qe-metric-label", classes="qe-list-label")
            yield ListView(id="qe-metrics", classes="qe-list")
            yield Static(
                "Direct series (0)",
                id="qe-series-label",
                classes="qe-list-label",
            )
            yield ListView(id="qe-series", classes="qe-list")
            yield Static("SCOPE", classes="qe-section")
            with Horizontal(id="qe-scope-row"):
                yield Label("Frequency", classes="qe-field-label")
                yield _EnumField(
                    FREQUENCIES,
                    labels={None: "Any"},
                    value=None,
                    id="qe-freq",
                )
            with Horizontal(id="qe-obs-row"):
                yield Label("Observations", classes="qe-field-label")
                yield _EnumField(
                    list(OBS_MODES),
                    labels=OBS_LABELS,
                    value="most_recent",
                    id="qe-obs-mode",
                )
                yield Input(value="20", id="qe-count", classes="-show")
            yield Static("─" * 48, id="qe-divider")
            yield Static("", id="qe-coverage", markup=True)
            with Horizontal(id="qe-actions"):
                yield Button("Update query", id="qe-update", variant="success")
                yield Button("Save as new", id="qe-save-new", variant="default")
                yield Button("Cancel", id="qe-cancel", variant="default")
            yield Static(
                "tab navigate · enter add/inspect · backspace remove · "
                "ctrl+s update · esc cancel",
                id="qe-hints",
            )

    def _focused(self):
        """Focused widget under this editor (widgets have no ``.focused``)."""
        try:
            focused = self.screen.focused
        except Exception:  # noqa: BLE001
            focused = getattr(self.app, "focused", None)
        if focused is not None and (focused is self or self in focused.ancestors):
            return focused
        return None

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self._focused(), Input):
            if action in {
                "remove_resource",
                "activate",
                "add_entity",
                "add_metric",
                "add_series",
            }:
                return False
        return True

    def show_editor(
        self,
        query: Query,
        *,
        investigation: Investigation,
        app_dir: Optional[Path] = None,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        self.edit_query = query
        self.investigation = investigation
        self.app_dir = app_dir
        self.project_id = project_id
        self.investigation_id = investigation_id
        self._labels = dict(labels or {})
        self._metric = list(query.metric)
        self._entity = list(query.entity)
        self._series = list(query.series)
        self._name = query.name or ""
        freq = query.frequency
        if freq is not None:
            freq = next(
                (f for f in FREQUENCIES if f and f.lower() == str(freq).lower()),
                freq,
            )
            if freq not in FREQUENCIES:
                freq = None
        self._frequency = freq
        self._obs_mode, self._obs_count = _obs_from_query(query)
        if self._obs_mode != "all" and self._obs_count is None:
            self._obs_count = 20
        self._baseline = self._snapshot()
        self._coverage = None
        self._show_matrix = False
        self.add_class("-visible")
        self.display = True
        self._paint_all()
        try:
            self.query_one("#qe-name", Input).focus()
        except Exception:  # noqa: BLE001
            pass
        self._refresh_coverage()

    def hide_editor(self) -> None:
        self.remove_class("-visible")
        self.display = False
        self.edit_query = None

    def _snapshot(self) -> _Snapshot:
        return _Snapshot(
            name=self._name.strip(),
            metric=tuple(self._metric),
            entity=tuple(self._entity),
            series=tuple(self._series),
            frequency=self._frequency,
            obs_mode=self._obs_mode,
            obs_count=self._obs_count if self._obs_mode != "all" else None,
        )

    def _is_dirty(self) -> bool:
        if self._baseline is None:
            return False
        return self._snapshot() != self._baseline

    def _field_dirty(self, field: str) -> bool:
        if self._baseline is None:
            return False
        cur = self._snapshot()
        return getattr(cur, field) != getattr(self._baseline, field)

    def _label(self, rid: str) -> str:
        return label_for(self._labels, rid)

    def _paint_all(self) -> None:
        q = self.edit_query
        tag = f"Q{q.id}" if q and q.id else "Draft"
        dirty_mark = "  [dim]· unsaved[/]" if self._is_dirty() else ""
        self.query_one("#qe-heading", Static).update(
            f"Edit query · {tag}{dirty_mark}"
        )
        name_input = self.query_one("#qe-name", Input)
        if name_input.value != self._name:
            name_input.value = self._name
        name_input.set_class(self._field_dirty("name"), "-dirty")

        self._paint_list("qe-entities", self._entity, "entity", "qe-entity-label", "Entities")
        self._paint_list("qe-metrics", self._metric, "metric", "qe-metric-label", "Metrics")
        self._paint_list("qe-series", self._series, "series", "qe-series-label", "Direct series")

        freq = self.query_one("#qe-freq", _EnumField)
        freq.set_value(self._frequency if self._frequency in FREQUENCIES else None)
        obs = self.query_one("#qe-obs-mode", _EnumField)
        obs.set_value(self._obs_mode)
        count = self.query_one("#qe-count", Input)
        show_count = self._obs_mode != "all"
        count.set_class(show_count, "-show")
        if show_count:
            count.value = str(self._obs_count or 20)
        count.set_class(self._field_dirty("obs_count") or self._field_dirty("obs_mode"), "-dirty")
        self.dirty = self._is_dirty()
        self._paint_coverage()

    def _paint_list(
        self,
        list_id: str,
        ids: list[str],
        kind: str,
        label_id: str,
        title: str,
    ) -> None:
        field = {"entity": "entity", "metric": "metric", "series": "series"}[kind]
        dirty_set = set()
        if self._baseline is not None:
            base = set(getattr(self._baseline, field))
            dirty_set = set(ids) - base
        listing = self.query_one(f"#{list_id}", ListView)
        listing.clear()
        for rid in ids:
            listing.append(
                _ResourceRow(
                    rid,
                    self._label(rid),
                    dirty=rid in dirty_set,
                    kind=kind,
                )
            )
        listing.append(_ResourceRow("", "", add=True, kind=kind))
        self.query_one(f"#{label_id}", Static).update(f"{title} ({len(ids)})")

    def _read_inputs(self) -> None:
        self._name = self.query_one("#qe-name", Input).value
        freq = self.query_one("#qe-freq", _EnumField).value
        self._frequency = freq
        self._obs_mode = self.query_one("#qe-obs-mode", _EnumField).value
        raw = self.query_one("#qe-count", Input).value.strip()
        if self._obs_mode == "all":
            self._obs_count = None
        elif raw.isdigit() and int(raw) > 0:
            self._obs_count = int(raw)
        else:
            self._obs_count = 20

    def _working_query(self) -> Query:
        self._read_inputs()
        head, tail = _head_tail(self._obs_mode, self._obs_count)
        q = self.edit_query or Query(id=0)
        return Query(
            id=q.id,
            name=self._name.strip() or q.name,
            metric=list(self._metric),
            entity=list(self._entity),
            series=list(self._series),
            frequency=self._frequency,
            head=head,
            tail=tail,
            annotations=q.annotations,
            notes_on=list(q.notes_on or []),
            in_report=q.in_report,
        )

    @on(Input.Changed, "#qe-name")
    def _name_changed(self, event: Input.Changed) -> None:
        self._name = event.value
        self.dirty = self._is_dirty()
        self.query_one("#qe-name", Input).set_class(self._field_dirty("name"), "-dirty")
        tag = f"Q{self.edit_query.id}" if self.edit_query else "Draft"
        dirty_mark = "  [dim]· unsaved[/]" if self.dirty else ""
        self.query_one("#qe-heading", Static).update(f"Edit query · {tag}{dirty_mark}")

    @on(Input.Changed, "#qe-count")
    def _count_changed(self, event: Input.Changed) -> None:
        raw = event.value.strip()
        if raw.isdigit() and int(raw) > 0:
            self._obs_count = int(raw)
        self.dirty = self._is_dirty()

    @on(_EnumField.Changed)
    def _enum_changed(self, event: _EnumField.Changed) -> None:
        control = event.control
        if control.id == "qe-freq":
            self._frequency = control.value
        elif control.id == "qe-obs-mode":
            self._obs_mode = control.value
            if self._obs_mode != "all" and not self._obs_count:
                self._obs_count = 20
            count = self.query_one("#qe-count", Input)
            show = self._obs_mode != "all"
            count.set_class(show, "-show")
            if show:
                count.value = str(self._obs_count or 20)
        self.dirty = self._is_dirty()
        self._refresh_coverage()

    @on(ListView.Selected)
    def _list_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _ResourceRow) and item.is_add:
            self._open_picker(item.kind)

    def action_activate(self) -> None:
        focused = self._focused()
        if isinstance(focused, ListView):
            item = focused.highlighted_child
            if isinstance(item, _ResourceRow):
                if item.is_add:
                    self._open_picker(item.kind)
                    return
                # IDs via inspection — labels stay primary.
                self.notify(f"{item.kind}: {item.resource_id}", timeout=3)
                return
        if isinstance(focused, (Input, _EnumField, Button)):
            return
        self._show_matrix = not self._show_matrix
        self._paint_coverage()

    def action_remove_resource(self) -> None:
        focused = self._focused()
        if not isinstance(focused, ListView):
            return
        item = focused.highlighted_child
        if not isinstance(item, _ResourceRow) or item.is_add:
            return
        rid = item.resource_id
        kind = item.kind
        if kind == "entity" and rid in self._entity:
            self._entity.remove(rid)
        elif kind == "metric" and rid in self._metric:
            self._metric.remove(rid)
        elif kind == "series" and rid in self._series:
            self._series.remove(rid)
        self._paint_all()
        self._refresh_coverage()

    def action_add_entity(self) -> None:
        self._open_picker("entity")

    def action_add_metric(self) -> None:
        self._open_picker("metric")

    def action_add_series(self) -> None:
        self._open_picker("series")

    def _open_picker(self, kind: str) -> None:
        from .resource_picker import ResourcePickerModal

        if kind not in {"entity", "metric", "series"}:
            return

        # Seed from the first selected resource so further picks stay in coverage.
        scope_metric = self._metric[0] if self._metric else None
        scope_entity = self._entity[0] if self._entity else None
        scope_metric_kw = scope_metric if kind == "entity" else None
        scope_entity_kw = scope_entity if kind == "metric" else None
        scope_label = None
        if scope_metric_kw:
            scope_label = self._label(scope_metric_kw)
        elif scope_entity_kw:
            scope_label = self._label(scope_entity_kw)
        exclude = (
            self._entity
            if kind == "entity"
            else self._metric
            if kind == "metric"
            else self._series
        )

        def _done(result: object | None) -> None:
            if not isinstance(result, dict):
                return
            rid = str(result.get("id") or "")
            label = str(result.get("label") or rid)
            if not rid:
                return
            remember = getattr(self.app, "remember_label", None)
            if callable(remember):
                remember(rid, label)
            self._labels[rid] = label
            if kind == "entity" and rid not in self._entity:
                self._entity.append(rid)
            elif kind == "metric" and rid not in self._metric:
                self._metric.append(rid)
            elif kind == "series" and rid not in self._series:
                self._series.append(rid)
            self._paint_all()
            self._refresh_coverage()

        self.app.push_screen(
            ResourcePickerModal(
                self.client,
                kind,  # type: ignore[arg-type]
                app_dir=self.app_dir,
                project_id=self.project_id,
                investigation_id=self.investigation_id,
                scope_metric=scope_metric_kw,
                scope_entity=scope_entity_kw,
                scope_label=scope_label,
                exclude_ids=exclude,
            ),
            _done,
        )

    def _refresh_coverage(self) -> None:
        self._paint_coverage_pending()
        self._coverage_worker()

    def _paint_coverage_pending(self) -> None:
        cov = self.query_one("#qe-coverage", Static)
        if not self._metric and not self._entity and not self._series:
            cov.update("[dim]Add entities and metrics to see coverage[/]")
            cov.set_class(False, "-ok")
            cov.set_class(False, "-warn")
            cov.set_class(False, "-err")
            return
        cov.update("[dim]Resolving coverage…[/]")

    @work(exclusive=True, group="qe-coverage")
    async def _coverage_worker(self) -> None:
        import asyncio

        if not self._metric and not self._entity and not self._series:
            self._coverage = None
            self._paint_coverage()
            return
        draft = self._working_query()
        try:
            result = await asyncio.to_thread(draft.inspect, self.client)
        except Exception as exc:  # noqa: BLE001
            self._coverage = {"error": str(exc)}
            self._paint_coverage()
            return
        self._coverage = result
        self._paint_coverage()

    def _paint_coverage(self) -> None:
        cov = self.query_one("#qe-coverage", Static)
        cov.set_class(False, "-ok")
        cov.set_class(False, "-warn")
        cov.set_class(False, "-err")
        data = self._coverage
        if not self._metric and not self._entity and not self._series:
            cov.update("[dim]Add entities and metrics to see coverage[/]")
            return
        if data is None:
            cov.update("[dim]Resolving coverage…[/]")
            return
        if "error" in data:
            cov.set_class(True, "-err")
            cov.update(f"! Coverage check failed: {data['error']}")
            return

        counts = data.get("counts") or {}
        expected = int(counts.get("expected_pairs") or 0)
        resolved = int(counts.get("resolved") or 0)
        series_n = int(counts.get("series") or 0)
        missing_n = int(counts.get("missing") or 0)
        direct = len(self._series)

        lines: list[str] = []
        if expected == 0 and direct:
            lines.append(f"✓ {direct} direct series")
            cov.set_class(True, "-ok")
        elif expected == 0:
            lines.append("[dim]No entity×metric pairs yet[/]")
        elif missing_n == 0:
            lines.append(f"✓ {resolved} of {expected} series available")
            cov.set_class(True, "-ok")
        else:
            lines.append(
                f"! {resolved} of {expected} series available · enter to inspect"
            )
            cov.set_class(True, "-warn")

        # Coverage span from resolved rows
        resolved_rows = data.get("resolved") or []
        earliest_vals = [r.get("earliest") for r in resolved_rows if r.get("earliest")]
        latest_vals = [r.get("latest") for r in resolved_rows if r.get("latest")]
        if earliest_vals and latest_vals:
            lo = min(str(x)[:10] for x in earliest_vals)
            hi = max(str(x)[:10] for x in latest_vals)
            lines.append(f"  Available coverage: {lo} – {hi}")

        if direct and expected:
            lines.append(f"  + {direct} direct series")

        if self._show_matrix and self._entity and self._metric:
            lines.append("")
            lines.append(self._format_matrix(data))

        cov.update("\n".join(lines))

    def _format_matrix(self, data: dict[str, Any]) -> str:
        found = {
            (r.get("metric"), r.get("entity"))
            for r in (data.get("resolved") or [])
        }
        # Header
        metrics = list(self._metric)
        entities = list(self._entity)
        col_w = max(12, max((len(self._label(m)[:16]) for m in metrics), default=12))
        header = " " * 18 + "".join(f"{self._label(m)[:16]:^{col_w}}" for m in metrics)
        rows = [header]
        for e in entities:
            cells = []
            for m in metrics:
                mark = "✓" if (m, e) in found else "—"
                cells.append(f"{mark:^{col_w}}")
            rows.append(f"{self._label(e)[:16]:<18}" + "".join(cells))
        return "\n".join(rows)

    def action_apply_update(self) -> None:
        self._apply(save_as_new=False)

    def action_save_as_new(self) -> None:
        self._apply(save_as_new=True)

    def action_cancel_edit(self) -> None:
        self.post_message(self.Cancelled())

    @on(Button.Pressed, "#qe-update")
    def _btn_update(self) -> None:
        self._apply(save_as_new=False)

    @on(Button.Pressed, "#qe-save-new")
    def _btn_save_new(self) -> None:
        self._apply(save_as_new=True)

    @on(Button.Pressed, "#qe-cancel")
    def _btn_cancel(self) -> None:
        self.post_message(self.Cancelled())

    def _apply(self, *, save_as_new: bool) -> None:
        if self.investigation is None or self.edit_query is None:
            return
        draft = self._working_query()
        if save_as_new:
            q = add_query(
                self.investigation,
                name=draft.name or f"Query {self.edit_query.id} copy",
                metric=list(draft.metric),
                entity=list(draft.entity),
                series=list(draft.series),
                frequency=draft.frequency,
                head=draft.head,
                tail=draft.tail,
                app_dir=self.app_dir,
                persist=False,
            )
            q.in_report = True
            persist_investigation(self.investigation, app_dir=self.app_dir)
            self.post_message(self.Applied(q.id, "new"))
            return

        q = self.edit_query
        q.name = draft.name or q.name
        q.metric = list(draft.metric)
        q.entity = list(draft.entity)
        q.series = list(draft.series)
        q.frequency = draft.frequency
        q.head = draft.head
        q.tail = draft.tail
        persist_investigation(self.investigation, app_dir=self.app_dir)
        self._baseline = self._snapshot()
        self.post_message(self.Applied(q.id, "update"))


# Backward-compatible name used by older imports (host no longer opens modal).
QueryEditModal = QueryEditor
