"""Query View — run a saved query and inspect observations.

Idle until ``r``. Results stay JSON records under the hood (one per series);
the human surface is a formatted table, optional chart, and a Raw JSON mode.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static
from textual_plotext import PlotextPlot

from ..client import JSTDataClient
from ..investigation.model import Query, Workspace
from ..models import TimeSeries
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    register,
    stage_ids,
)
from .screen import BaseScreen

MODES = ("table", "chart", "raw")


def series_to_record(ts: TimeSeries) -> dict[str, Any]:
    """JSON-ready record for one ``TimeSeries`` (metadata + observations)."""
    series = ts.series
    observations = [
        {
            "observation_timestamp": obs.observation_timestamp.isoformat(),
            "release_timestamp": obs.release_timestamp.isoformat(),
            "value": obs.value,
            "release_id": obs.release_id,
        }
        for obs in ts.observations
    ]
    return {
        "id": series.id,
        "label": series.label,
        "metric_id": series.metric_id,
        "entities": [{"id": e.id, "label": e.label} for e in series.entities],
        "frequency": series.frequency,
        "units": series.units,
        "source": series.source,
        "seasonal_adjustment": series.seasonal_adjustment,
        "n_observations": len(observations),
        "last": observations[-1] if observations else None,
        "observations": observations,
    }


def _parse_stamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).replace("Z", "+00:00").replace(" ", "T")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_value(value: Any, *, digits: int = 2) -> str:
    """Human-facing number; raw precision stays on the record."""
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:  # NaN
        return "—"
    formatted = f"{number:,.{digits}f}"
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted or "0"


def format_period(stamp: Any, frequency: Optional[str] = None) -> str:
    dt = _parse_stamp(stamp)
    if dt is None:
        return "—"
    freq = (frequency or "").lower()
    if "quarter" in freq:
        return f"{dt.year} Q{(dt.month - 1) // 3 + 1}"
    if "annual" in freq or freq in {"yearly", "year"}:
        return str(dt.year)
    if "month" in freq:
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def format_release(stamp: Any, *, short: bool = False) -> str:
    dt = _parse_stamp(stamp)
    if dt is None:
        return "—"
    if short:
        return dt.strftime("%b %Y")
    return dt.strftime("%Y-%m-%d")


def record_title(record: dict[str, Any]) -> str:
    entities = record.get("entities") or []
    entity = entities[0]["label"] if entities else ""
    label = record.get("label") or record.get("metric_id") or record.get("id") or "Series"
    if entity and entity.lower() not in label.lower():
        return f"{label} · {entity}"
    return str(label)


def query_spec_line(query: Query) -> str:
    bits = [
        f"{len(query.metric)} metric" + ("" if len(query.metric) == 1 else "s"),
        f"{len(query.entity)} entit"
        + ("y" if len(query.entity) == 1 else "ies"),
        f"{len(query.series)} series",
    ]
    if query.frequency:
        bits.append(query.frequency)
    if query.source:
        bits.append(query.source)
    bits.append("cartesian")
    return " · ".join(bits)


CHART_DATE_FORM = "Y-m-d"
CHART_LINE_COLOR = (134, 239, 172)  # #86efac, matches the query chrome


def history_points(record: dict[str, Any]) -> tuple[list[str], list[float]]:
    """Chronological date strings and values for plotext; gaps are dropped."""
    points: list[tuple[datetime, float]] = []
    for obs in record.get("observations") or []:
        raw = obs.get("value")
        if raw is None:
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if number != number:  # NaN
            continue
        dt = _parse_stamp(obs.get("observation_timestamp"))
        if dt is None:
            continue
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        points.append((dt, number))
    points.sort(key=lambda item: item[0])
    times = [dt.strftime("%Y-%m-%d") for dt, _ in points]
    values = [value for _, value in points]
    return times, values


def plot_history(plt: Any, record: Optional[dict[str, Any]]) -> bool:
    """Draw one series onto a plotext figure.

    Returns True when a line (or scatter) was drawn.
    """
    plt.clear_data()
    plt.title("")
    plt.xlabel("")
    plt.ylabel("")
    times, values = history_points(record or {})
    if not times:
        plt.title("No observations")
        return False
    units = str(record.get("units") or "").strip() if record else ""
    plt.date_form(CHART_DATE_FORM)
    plt.ylabel(units)
    draw = plt.scatter if len(values) == 1 else plt.plot
    draw(times, values, marker="braille", color=CHART_LINE_COLOR)
    return True


def observation_detail_copy(record: dict[str, Any], obs: dict[str, Any]) -> str:
    freq = record.get("frequency")
    period = format_period(obs.get("observation_timestamp"), freq)
    value = format_value(obs.get("value"))
    entities = record.get("entities") or []
    entity = entities[0]["label"] if entities else "—"
    rows = [
        ("Series", record.get("id") or "—"),
        ("Metric", record.get("metric_id") or record.get("label") or "—"),
        ("Entity", entity),
        ("Observation date", format_release(obs.get("observation_timestamp"))),
        ("Release date", format_release(obs.get("release_timestamp"))),
        ("Source", record.get("source") or "—"),
        ("Units", record.get("units") or "—"),
        ("Release id", obs.get("release_id") or "—"),
    ]
    lines = [
        f"[bold]{period}  ·  {value}[/]",
        "",
    ]
    for label, val in rows:
        lines.append(f"[#888]{label:<20}[/]{val}")
    return "\n".join(lines)


class ObservationRow(ListItem):
    def __init__(self, record: dict[str, Any], obs: dict[str, Any]) -> None:
        super().__init__()
        self.record = record
        self.obs = obs

    def compose(self) -> ComposeResult:
        freq = self.record.get("frequency")
        period = format_period(self.obs.get("observation_timestamp"), freq)
        value = format_value(self.obs.get("value"))
        released = format_release(self.obs.get("release_timestamp"))
        yield Label(
            f"{period:<14}{value:>14}{released:>16}",
            classes="obs-row",
        )


class SeriesNavRow(ListItem):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        last = self.record.get("last") or {}
        entities = self.record.get("entities") or []
        name = entities[0]["label"] if entities else (self.record.get("label") or self.record.get("id"))
        value = format_value(last.get("value"))
        yield Label(f"{name:<22}{value:>10}", classes="series-nav-row")


class ObservationDetail(ModalScreen[None]):
    DEFAULT_CSS = """
    ObservationDetail {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #obs-box {
        width: 64;
        height: auto;
        max-height: 22;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #obs-kicker { color: #4ade80; text-style: bold; height: 1; }
    #obs-body { color: #e0e0e0; height: auto; margin-top: 1; }
    #obs-hint { color: #888; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "close", "Close", show=False)]

    def __init__(self, record: dict[str, Any], obs: dict[str, Any]) -> None:
        super().__init__()
        self.record = record
        self.obs = obs

    def compose(self) -> ComposeResult:
        with Vertical(id="obs-box"):
            yield Label("OBSERVATION DETAIL", id="obs-kicker")
            yield Static(observation_detail_copy(self.record, self.obs), id="obs-body")
            yield Label("[bold]esc[/bold] close", id="obs-hint", markup=True)

    def action_close(self) -> None:
        self.dismiss(None)


class HistoryPlot(PlotextPlot):
    """Plotext line chart for one query series."""

    can_focus = False

    def plot_record(self, record: Optional[dict[str, Any]]) -> None:
        plot_history(self.plt, record)
        self.refresh()


class QueryScreen(BaseScreen):
    """Observation workbench for a saved (or workspace) query."""

    BINDINGS = [
        Binding("r", "load", "Run query", key_display="r"),
        Binding("t", "mode_table", "Table", show=False),
        Binding("c", "mode_chart", "Chart", show=False),
        Binding("v", "mode_raw", "Raw JSON", show=False),
        Binding("space", "collect", "Collect into workspace", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("enter", "inspect", "Inspect", priority=True),
    ]

    FOOTER_CHROME: tuple[str, ...] = (
        "[bold #e0e0e0]:[/] [#888]commands[/]",
        "[bold #e0e0e0]g[/] [#888]home[/]",
        "[bold #e0e0e0]?[/] [#888]help[/]",
        "[bold #e0e0e0]q[/] [#888]quit[/]",
    )

    DEFAULT_CSS = """
    #query-body { height: 1fr; padding: 1 2 0 2; }
    #query-kicker { color: #4ade80; text-style: bold; height: 1; }
    #query-title { color: #e0e0e0; text-style: bold; height: 1; }
    #query-spec { color: #888; height: 1; margin-bottom: 1; }
    #query-status { color: #888; height: 1; margin-bottom: 1; }
    #query-stats { height: 4; margin-bottom: 1; }
    .stat-card {
        width: 1fr;
        height: 4;
        background: #111a14;
        padding: 0 1;
        margin-right: 1;
    }
    .stat-label { color: #4ade80; height: 1; }
    .stat-value { color: #f5f5f5; text-style: bold; height: 1; }
    .stat-meta { color: #888; height: 1; }
    #query-main { height: 1fr; }
    #query-series {
        width: 32;
        height: 1fr;
        background: #0f0f0f;
        display: none;
        border-right: solid #222;
    }
    #query-pane { width: 1fr; height: 1fr; }
    #query-pane-kicker { color: #4ade80; text-style: bold; height: 1; margin-bottom: 1; }
    #query-obs-header { color: #888; height: 1; }
    #query-obs { height: 1fr; background: #0a0a0a; }
    #query-chart {
        height: 1fr;
        width: 1fr;
        display: none;
    }
    #query-raw {
        height: 1fr;
        color: #c0c0c0;
        display: none;
    }
    .obs-row { width: 100%; height: 1; color: #e0e0e0; }
    .series-nav-row { width: 100%; height: 1; color: #d0d0d0; }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    ObservationRow, SeriesNavRow {
        height: 1;
        min-height: 1;
        max-height: 1;
        border-left: solid #0a0a0a;
        padding: 0 1 0 0;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        query_id: Optional[int] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.query_id = int(query_id) if query_id is not None else None
        self._records: list[dict[str, Any]] = []
        self._loaded = False
        self._mode = "table"
        self._series_i = 0

    def compose_body(self) -> ComposeResult:
        with Vertical(id="query-body"):
            yield Label("QUERY", id="query-kicker")
            yield Label("", id="query-title")
            yield Static("", id="query-spec")
            yield Static(
                "Idle — press [bold]r[/bold] to run",
                id="query-status",
                markup=True,
            )
            with Horizontal(id="query-stats"):
                with Vertical(classes="stat-card", id="stat-latest"):
                    yield Label("LATEST VALUE", classes="stat-label")
                    yield Label("—", id="stat-latest-value", classes="stat-value")
                    yield Label("", id="stat-latest-meta", classes="stat-meta")
                with Vertical(classes="stat-card", id="stat-n"):
                    yield Label("OBSERVATIONS", classes="stat-label")
                    yield Label("—", id="stat-n-value", classes="stat-value")
                    yield Label("", id="stat-n-meta", classes="stat-meta")
                with Vertical(classes="stat-card", id="stat-release"):
                    yield Label("LATEST RELEASE", classes="stat-label")
                    yield Label("—", id="stat-release-value", classes="stat-value")
                    yield Label("Source timestamp", classes="stat-meta")
            with Horizontal(id="query-main"):
                yield ListView(id="query-series")
                with Vertical(id="query-pane"):
                    yield Label("OBSERVATIONS", id="query-pane-kicker")
                    yield Label(
                        f"{'Period':<14}{'Value':>14}{'Released':>16}",
                        id="query-obs-header",
                    )
                    yield ListView(id="query-obs")
                    yield HistoryPlot(id="query-chart")
                    with VerticalScroll(id="query-raw"):
                        yield Static("", id="query-raw-body")

    def on_mount(self) -> None:
        super().on_mount()
        self._render_idle()
        self._apply_mode()
        self.query_one("#query-obs", ListView).focus()

    def footer_status(self) -> list[str]:
        return []

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold #e0e0e0]r[/] [#888]run[/]"]
        if self._loaded:
            keys.extend(
                [
                    "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]move[/]",
                    "[bold #e0e0e0]t[/] [#888]table[/]",
                    "[bold #e0e0e0]c[/] [#888]chart[/]",
                    "[bold #e0e0e0]v[/] [#888]raw[/]",
                    "[bold #e0e0e0]↵[/] [#888]inspect[/]",
                    "[bold #e0e0e0]space[/] [#888]collect[/]",
                ]
            )
        return keys

    def refresh_footer(self) -> None:
        parts = [*self.footer_action_keys(), *self.FOOTER_CHROME]
        self.query_one("#help-hint", Label).update("    ".join(parts))

    def _resolve_query(self) -> Query:
        inv = getattr(self.app, "investigation", None)
        if self.query_id is not None:
            if inv is None:
                raise RuntimeError("No investigation loaded")
            return inv.get_query(self.query_id)
        return Query.from_workspace(self.workspace, query_id=0, name="workspace")

    def _render_idle(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.query_one("#query-kicker", Label).update("QUERY")
            self.query_one("#query-title", Label).update(str(exc))
            return
        tag = f"Q{query.id}" if query.id else "WORKSPACE"
        title = query.name or tag
        self.query_one("#query-kicker", Label).update(f"QUERY / {tag}")
        self.query_one("#query-title", Label).update(title)
        self.query_one("#query-spec", Static).update(query_spec_line(query))
        self.query_one("#query-stats").display = False

    def action_load(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        if query.is_empty():
            self.notify(
                "Query is empty — Search / Collect into the workspace first",
                severity="warning",
            )
            return
        self.load_query(query)

    @work(exclusive=True, group="query-view")
    async def load_query(self, query: Query) -> None:
        status = self.query_one("#query-status", Static)
        status.update("Running /query…")
        try:
            results = await asyncio.to_thread(query.execute, self.client)
        except Exception as exc:
            status.update(f"Query failed: {exc}")
            return
        self._records = [series_to_record(ts) for ts in results]
        self._loaded = True
        self._series_i = 0
        self._mode = "chart" if len(self._records) == 1 else "table"
        n = len(self._records)
        status.update(f"{n} series returned" if n else "No series returned")
        self._fill_series_nav()
        self._show_selected()
        self.refresh_footer()

    def _fill_series_nav(self) -> None:
        nav = self.query_one("#query-series", ListView)
        nav.clear()
        multi = len(self._records) > 1
        nav.display = multi
        if not multi:
            return
        for record in self._records:
            nav.append(SeriesNavRow(record))
        if self._records:
            nav.index = min(self._series_i, len(self._records) - 1)

    def _selected_record(self) -> Optional[dict[str, Any]]:
        if not self._records:
            return None
        return self._records[min(self._series_i, len(self._records) - 1)]

    def _show_selected(self) -> None:
        record = self._selected_record()
        stats = self.query_one("#query-stats")
        if record is None:
            stats.display = False
            self.query_one("#query-obs", ListView).clear()
            self.query_one("#query-chart", HistoryPlot).plot_record(None)
            self.query_one("#query-raw-body", Static).update("{}")
            self._apply_mode()
            return
        stats.display = True
        last = record.get("last") or {}
        freq = record.get("frequency") or "—"
        units = record.get("units") or ""
        n = int(record.get("n_observations") or 0)
        self.query_one("#query-title", Label).update(record_title(record))
        self.query_one("#stat-latest-value", Label).update(
            format_value(last.get("value"))
        )
        self.query_one("#stat-latest-meta", Label).update(
            f"{format_period(last.get('observation_timestamp'), freq)}  ·  {units}".strip(" ·")
        )
        self.query_one("#stat-n-value", Label).update(str(n))
        self.query_one("#stat-n-meta", Label).update(str(freq))
        self.query_one("#stat-release-value", Label).update(
            format_release(last.get("release_timestamp"), short=True)
        )

        obs_list = self.query_one("#query-obs", ListView)
        obs_list.clear()
        rows = list(reversed(record.get("observations") or []))
        for obs in rows:
            obs_list.append(ObservationRow(record, obs))
        self.query_one("#query-raw-body", Static).update(
            json.dumps(record, indent=2, default=str)
        )
        self.query_one("#query-chart", HistoryPlot).plot_record(record)
        self._apply_mode()

    def _apply_mode(self) -> None:
        table = self._mode == "table"
        chart = self._mode == "chart"
        raw = self._mode == "raw"
        self.query_one("#query-obs-header").display = table
        self.query_one("#query-obs").display = table
        self.query_one("#query-chart").display = chart
        self.query_one("#query-raw").display = raw
        kicker = {
            "table": "OBSERVATIONS     Latest first",
            "chart": "HISTORICAL OBSERVATIONS     Chart",
            "raw": "RAW JSON",
        }[self._mode]
        self.query_one("#query-pane-kicker", Label).update(kicker)
        if table:
            self.query_one("#query-obs", ListView).focus()
        elif len(self._records) > 1:
            self.query_one("#query-series", ListView).focus()

    def action_mode_table(self) -> None:
        self._mode = "table"
        self._apply_mode()

    def action_mode_chart(self) -> None:
        self._mode = "chart"
        self._apply_mode()

    def action_mode_raw(self) -> None:
        self._mode = "raw"
        self._apply_mode()

    def action_inspect(self) -> None:
        if self._mode != "table":
            record = self._selected_record()
            if record and record.get("last"):
                self.app.push_screen(ObservationDetail(record, record["last"]))
            return
        child = self.query_one("#query-obs", ListView).highlighted_child
        if isinstance(child, ObservationRow):
            self.app.push_screen(ObservationDetail(child.record, child.obs))
            return
        self.notify("No observation highlighted", severity="warning")

    def action_cursor_down(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView) and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()
            return
        self.query_one("#query-obs", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        focused = self.focused
        if isinstance(focused, ListView) and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()
            return
        self.query_one("#query-obs", ListView).action_cursor_up()

    def action_collect(self) -> None:
        record = self._selected_record()
        if record is None:
            self.notify("No series highlighted", severity="warning")
            return
        entities = record.get("entities") or []
        entity_id = entities[0]["id"] if entities else None
        labels = {}
        if entity_id:
            labels[entity_id] = entities[0].get("label") or entity_id
        if record.get("metric_id"):
            labels[record["metric_id"]] = record.get("label") or record["metric_id"]
        if record.get("id"):
            labels[record["id"]] = record.get("label") or record["id"]
        added = stage_ids(
            self.workspace,
            self.app,
            metric=record.get("metric_id"),
            entity=entity_id,
            series=record.get("id"),
            labels=labels,
        )
        if not added:
            self.notify("Already in workspace", severity="warning")
            return
        self.notify("Collected " + ", ".join(added))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {"collect", "inspect", "mode_table", "mode_chart", "mode_raw"}:
            return self._loaded
        return True

    @on(ListView.Highlighted, "#query-series")
    def on_series_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SeriesNavRow):
            try:
                self._series_i = self._records.index(event.item.record)
            except ValueError:
                return
            self._show_selected()

    @on(ListView.Selected, "#query-obs")
    def on_obs_selected(self, event: ListView.Selected) -> None:
        self.action_inspect()


def create_query_screen(client, workspace, query_id=None, **kwargs):
    kwargs.pop("input_context", None)
    kwargs.pop("meta", None)
    return QueryScreen(client, workspace, query_id=query_id)


QUERY_VIEW = register(
    ActionSpec(
        id="query",
        name="Query",
        description=(
            "Run a saved cartesian query and inspect observations as a table, "
            "chart, or raw JSON. Idle until r; space collects."
        ),
        create_screen=create_query_screen,
        arguments=(
            ActionArgument(
                name="query_id",
                type="integer",
                description="Saved query id (omit to run the workspace bag)",
            ),
        ),
        bindings=(
            ActionBinding("r", "load", "Execute GET /query"),
            ActionBinding("t / c / v", "mode", "Table, chart, or raw JSON"),
            ActionBinding("j / k", "cursor", "Move series or observations"),
            ActionBinding("enter", "inspect", "Inspect the highlighted observation"),
            ActionBinding("space", "collect", "Collect metric/entity/series into workspace"),
        ),
        example="jst action add query --args '{\"query_id\":1}'",
    )
)
