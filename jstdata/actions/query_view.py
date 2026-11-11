"""Query View — run a saved query and inspect observations.

Idle until ``r``. Results stay JSON records under the hood (one per series);
the human surface is a formatted table, optional chart, and a Raw JSON mode.
"""

from __future__ import annotations

import asyncio
import csv
import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path
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
CHART_PALETTE = (
    (134, 239, 172),
    (96, 165, 250),
    (251, 191, 36),
    (244, 114, 182),
    (167, 139, 250),
    (248, 113, 113),
)
EXPORT_DIR = Path.home() / ".jstdata" / "exports"


@dataclass(frozen=True)
class PlotGroup:
    """Series that share a metric and units — overlay on one chart.

    Frequency may differ (France annual vs US quarterly for the same metric);
    the y-axis is what has to match.
    """

    metric_id: str
    frequency: str
    units: str
    records: tuple[dict[str, Any], ...]


def plot_group_key(record: dict[str, Any]) -> tuple[str, str]:
    metric = str(record.get("metric_id") or "").strip()
    units = str(record.get("units") or "").strip().casefold()
    if not metric:
        sid = str(record.get("id") or id(record))
        return (f"series:{sid}", units)
    return (metric, units)


def _shared_frequency(records: Sequence[dict[str, Any]]) -> str:
    freqs: list[str] = []
    for rec in records:
        freq = str(rec.get("frequency") or "").strip()
        if freq and freq not in freqs:
            freqs.append(freq)
    if not freqs:
        return ""
    if len(freqs) == 1:
        return freqs[0]
    return " / ".join(freqs)


def group_plot_records(records: Sequence[dict[str, Any]]) -> list[PlotGroup]:
    """One group per (metric, units) pair, in first-seen order."""
    buckets: OrderedDict[tuple[str, str], list[dict[str, Any]]] = OrderedDict()
    for rec in records:
        buckets.setdefault(plot_group_key(rec), []).append(rec)
    groups: list[PlotGroup] = []
    for items in buckets.values():
        rec = items[0]
        groups.append(
            PlotGroup(
                metric_id=str(rec.get("metric_id") or "").strip(),
                frequency=_shared_frequency(items),
                units=str(rec.get("units") or "").strip(),
                records=tuple(items),
            )
        )
    return groups


def plot_group_title(group: PlotGroup) -> str:
    rec = group.records[0]
    label = str(rec.get("label") or "")
    if " for " in label:
        return label.split(" for ", 1)[0].strip()
    if rec.get("metric_id"):
        return str(rec["metric_id"]).replace("-", " ").title()
    return label or "Series"


def plot_group_label(group: PlotGroup) -> str:
    bits = [plot_group_title(group)]
    if group.frequency:
        bits.append(group.frequency)
    if group.units:
        bits.append(group.units)
    return " · ".join(bits)


def series_line_label(record: dict[str, Any]) -> str:
    entities = record.get("entities") or []
    if entities:
        name = entities[0].get("label") or entities[0].get("id")
        if name:
            return str(name)
    return str(record.get("label") or record.get("id") or "Series")


def line_labels(records: Sequence[dict[str, Any]]) -> list[str]:
    labels = [series_line_label(r) for r in records]
    if len(set(labels)) == len(labels):
        return labels
    out: list[str] = []
    seen: dict[str, int] = {}
    for rec, label in zip(records, labels):
        extra = rec.get("source") or rec.get("id") or ""
        if labels.count(label) > 1 and extra:
            label = f"{label} · {extra}"
        n = seen.get(label, 0) + 1
        seen[label] = n
        out.append(label if n == 1 else f"{label} ({n})")
    return out


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def observation_stamps(record: dict[str, Any]) -> list[datetime]:
    stamps: list[datetime] = []
    for obs in record.get("observations") or []:
        dt = _parse_stamp(obs.get("observation_timestamp"))
        if dt is None:
            continue
        stamps.append(_naive(dt))
    stamps.sort()
    return stamps


def coverage_years(records: Sequence[dict[str, Any]]) -> str:
    """Human coverage span, e.g. ``2019–2024``."""
    years: list[int] = []
    for rec in records:
        for dt in observation_stamps(rec):
            years.append(dt.year)
    if not years:
        return "—"
    lo, hi = min(years), max(years)
    if lo == hi:
        return str(lo)
    return f"{lo}–{hi}"


def calendar_xticks(
    records: Sequence[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Year ticks for a multi-year span; otherwise actual observation dates."""
    stamps: list[datetime] = []
    for rec in records:
        stamps.extend(observation_stamps(rec))
    if not stamps:
        return [], []
    stamps.sort()
    years = list(range(stamps[0].year, stamps[-1].year + 1))
    if len(years) >= 3:
        return [f"{year}-01-01" for year in years], [str(year) for year in years]
    uniq: list[datetime] = []
    seen: set[str] = set()
    for dt in stamps:
        key = dt.strftime("%Y-%m-%d")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(dt)
    freq = str((records[0].get("frequency") if records else "") or "")
    if len(uniq) > 8:
        step = max(1, len(uniq) // 6)
        picked = [uniq[i] for i in range(0, len(uniq), step)]
        if picked[-1] is not uniq[-1]:
            picked.append(uniq[-1])
        uniq = picked
    return (
        [dt.strftime("%Y-%m-%d") for dt in uniq],
        [format_period(dt, freq) for dt in uniq],
    )


def cursor_dates(records: Sequence[dict[str, Any]]) -> list[datetime]:
    """Unique observation dates across series, chronological."""
    seen: set[str] = set()
    out: list[datetime] = []
    for rec in records:
        for dt in observation_stamps(rec):
            key = dt.strftime("%Y-%m-%d")
            if key in seen:
                continue
            seen.add(key)
            out.append(dt)
    out.sort()
    return out


def observation_on_date(
    record: dict[str, Any], day: datetime
) -> Optional[dict[str, Any]]:
    """Exact calendar-date match; None means the series is missing that period."""
    target = _naive(day).strftime("%Y-%m-%d")
    for obs in record.get("observations") or []:
        dt = _parse_stamp(obs.get("observation_timestamp"))
        if dt is None:
            continue
        if _naive(dt).strftime("%Y-%m-%d") == target:
            return obs
    return None


def query_python_snippet(query: Query) -> str:
    kwargs = query.to_query_kwargs()
    if not kwargs:
        args = ""
    else:
        args = ",\n".join(f"    {key}={value!r}" for key, value in kwargs.items())
        args = f"\n{args}\n"
    return (
        "from jstdata import JSTDataClient\n"
        "\n"
        "client = JSTDataClient()\n"
        f"df = client.query_df({args})\n"
        "print(df)\n"
    )


def records_to_csv(records: Sequence[dict[str, Any]]) -> str:
    """Long CSV: one row per observation. Missing values stay empty."""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "date",
            "series_id",
            "entity",
            "metric_id",
            "value",
            "units",
            "frequency",
            "source",
            "released",
        ]
    )
    for rec in records:
        entities = rec.get("entities") or []
        entity = entities[0]["id"] if entities else ""
        for obs in rec.get("observations") or []:
            dt = _parse_stamp(obs.get("observation_timestamp"))
            rel = _parse_stamp(obs.get("release_timestamp"))
            value = obs.get("value")
            writer.writerow(
                [
                    dt.strftime("%Y-%m-%d") if dt else "",
                    rec.get("id") or "",
                    entity,
                    rec.get("metric_id") or "",
                    "" if value is None else value,
                    rec.get("units") or "",
                    rec.get("frequency") or "",
                    rec.get("source") or "",
                    rel.strftime("%Y-%m-%d") if rel else "",
                ]
            )
    return buf.getvalue()


def coverage_status(query: Query, records: Sequence[dict[str, Any]]) -> str:
    """Loading/result line, including missing cartesian pairs."""
    n = len(records)
    expected = query.expected_pairs() if hasattr(query, "expected_pairs") else []
    if not expected:
        return f"{n} series returned" if n else "No series returned"
    got: set[tuple[str, str]] = set()
    for rec in records:
        metric = str(rec.get("metric_id") or "")
        for ent in rec.get("entities") or []:
            eid = str(ent.get("id") or "")
            if metric and eid:
                got.add((metric, eid))
    missing = [pair for pair in expected if pair not in got]
    if not n:
        return f"No series returned · {len(expected)} pair(s) expected"
    if missing:
        sample = ", ".join(f"{m} × {e}" for m, e in missing[:2])
        extra = f" +{len(missing) - 2}" if len(missing) > 2 else ""
        return f"{n} series · {len(missing)} pair(s) missing ({sample}{extra})"
    return f"{n} series · {len(expected)} pair(s) resolved"


def copy_text(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


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
        points.append((_naive(dt), number))
    points.sort(key=lambda item: item[0])
    times = [dt.strftime("%Y-%m-%d") for dt, _ in points]
    values = [value for _, value in points]
    return times, values


def plot_history(
    plt: Any,
    records: Optional[dict[str, Any] | Sequence[dict[str, Any]]] = None,
    *,
    cursor_date: Optional[datetime] = None,
    colors: Optional[Sequence[tuple[int, int, int]]] = None,
) -> bool:
    """Draw one or more series onto a plotext figure.

    Overlay series that share an axis (same units). Returns True when at
    least one line (or scatter) was drawn. X ticks are calendar years when
    the span is long enough; a cursor date draws a vertical marker.
    """
    if records is None:
        recs: list[dict[str, Any]] = []
    elif isinstance(records, dict):
        recs = [records]
    else:
        recs = list(records)
    plt.clear_data()
    plt.title("")
    plt.xlabel("")
    plt.ylabel("")
    drawn = 0
    units = ""
    names = line_labels(recs)
    for i, (rec, name) in enumerate(zip(recs, names)):
        times, values = history_points(rec)
        if not times:
            continue
        if not units:
            units = str(rec.get("units") or "").strip()
        plt.date_form(CHART_DATE_FORM)
        draw = plt.scatter if len(values) == 1 else plt.plot
        if colors and i < len(colors):
            color = colors[i]
        elif len(recs) == 1:
            color = CHART_LINE_COLOR
        else:
            color = CHART_PALETTE[i % len(CHART_PALETTE)]
        draw(times, values, marker="braille", label=name, color=color)
        drawn += 1
    if not drawn:
        plt.title("No observations")
        return False
    plt.ylabel(units)
    ticks, labels = calendar_xticks(recs)
    if ticks:
        plt.xticks(ticks, labels)
    if cursor_date is not None:
        plt.vline(_naive(cursor_date).strftime("%Y-%m-%d"))
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
        raw = self.obs.get("value")
        value = format_value(raw)
        released = format_release(self.obs.get("release_timestamp"))
        classes = "obs-row obs-missing" if raw is None else "obs-row"
        yield Label(
            f"{period:<14}{value:>14}{released:>16}",
            classes=classes,
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


class PlotGroupRow(ListItem):
    def __init__(self, group: PlotGroup) -> None:
        super().__init__()
        self.group = group

    def compose(self) -> ComposeResult:
        n = len(self.group.records)
        title = plot_group_title(self.group)
        meta = self.group.frequency or ""
        if n > 1:
            meta = f"{meta} ×{n}".strip()
        yield Label(f"{title:<18}{meta:>14}", classes="series-nav-row")


class LegendRow(ListItem):
    """One series in the current chart group (select / hide)."""

    def __init__(self, record: dict[str, Any], *, hidden: bool = False) -> None:
        super().__init__()
        self.record = record
        self.hidden = hidden

    def compose(self) -> ComposeResult:
        mark = "○" if self.hidden else "●"
        name = series_line_label(self.record)
        yield Label(f"{mark} {name}", classes="series-nav-row")


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
    """Plotext line chart for one comparable query group."""

    can_focus = False

    def plot_records(
        self,
        records: Optional[Sequence[dict[str, Any]]] = None,
        *,
        cursor_date: Optional[datetime] = None,
        colors: Optional[Sequence[tuple[int, int, int]]] = None,
    ) -> None:
        plot_history(
            self.plt, records, cursor_date=cursor_date, colors=colors
        )
        self.refresh()

    def plot_record(self, record: Optional[dict[str, Any]]) -> None:
        self.plot_records(None if record is None else [record])


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
        Binding("h", "obs_left", "Older observation", show=False),
        Binding("l", "obs_right", "Newer observation", show=False),
        Binding("left", "obs_left", "Older observation", show=False),
        Binding("right", "obs_right", "Newer observation", show=False),
        Binding("x", "toggle_series", "Hide or show series", show=False),
        Binding("o", "toggle_sort", "Sort observations", show=False),
        Binding("e", "export_csv", "Export CSV", show=False),
        Binding("y", "copy_python", "Copy Python", show=False),
        Binding("[", "prev_group", "Previous chart group", show=False),
        Binding("]", "next_group", "Next chart group", show=False),
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
    #query-spec { color: #888; height: 1; }
    #query-status { color: #888; height: 1; margin-bottom: 1; }
    #query-summary {
        height: 7;
        background: #111a14;
        padding: 0 1;
        margin-bottom: 1;
        display: none;
    }
    #sum-kicker { color: #4ade80; height: 1; }
    #sum-cols { height: 4; }
    #sum-left, #sum-right { width: 1fr; height: 4; }
    #sum-latest-kicker { color: #888; height: 1; }
    #sum-latest-value { color: #f5f5f5; text-style: bold; height: 1; }
    #sum-units { color: #888; height: 1; }
    #sum-cov-kicker { color: #888; height: 1; }
    #sum-cov-value { color: #f5f5f5; text-style: bold; height: 1; }
    #sum-cov-meta { color: #888; height: 1; }
    #sum-foot { color: #666; height: 1; }
    #query-cursor {
        height: 4;
        background: #111a14;
        padding: 0 1;
        margin-bottom: 1;
        display: none;
    }
    #cur-kicker { color: #4ade80; height: 1; }
    #cur-period { color: #e0e0e0; text-style: bold; height: 1; }
    #cur-meta { color: #888; height: 1; }
    #cur-compare { color: #c0c0c0; height: 1; }
    #cur-value { color: #86efac; text-style: bold; height: 1; text-align: right; }
    #cur-left { width: 1fr; }
    #cur-right { width: 16; }
    #query-main { height: 1fr; }
    #query-series {
        width: 28;
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
    .obs-missing { color: #666; }
    .series-nav-row { width: 100%; height: 1; color: #d0d0d0; }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    ObservationRow, SeriesNavRow, PlotGroupRow, LegendRow {
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
        self._groups: list[PlotGroup] = []
        self._loaded = False
        self._mode = "table"
        self._series_i = 0
        self._group_i = 0
        self._hidden: set[str] = set()
        self._cursor_i = -1
        self._newest_first = True
        self._busy = False

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
            with Vertical(id="query-summary"):
                yield Label("", id="sum-kicker")
                with Horizontal(id="sum-cols"):
                    with Vertical(id="sum-left"):
                        yield Label("", id="sum-latest-kicker")
                        yield Label("—", id="sum-latest-value")
                        yield Label("", id="sum-units")
                    with Vertical(id="sum-right"):
                        yield Label("Historical coverage", id="sum-cov-kicker")
                        yield Label("—", id="sum-cov-value")
                        yield Label("", id="sum-cov-meta")
                yield Label("", id="sum-foot")
            with Horizontal(id="query-cursor"):
                with Vertical(id="cur-left"):
                    yield Label("SELECTED OBSERVATION          h / l", id="cur-kicker")
                    yield Label("", id="cur-period")
                    yield Label("", id="cur-meta")
                    yield Label("", id="cur-compare")
                with Vertical(id="cur-right"):
                    yield Label("")
                    yield Label("—", id="cur-value")
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
                    "[bold #e0e0e0]t[/] [#888]table[/]",
                    "[bold #e0e0e0]c[/] [#888]chart[/]",
                    "[bold #e0e0e0]v[/] [#888]raw[/]",
                    "[bold #e0e0e0]j[/]/[bold #e0e0e0]k[/] [#888]move[/]",
                ]
            )
            if self._mode == "chart":
                keys.extend(
                    [
                        "[bold #e0e0e0]h[/]/[bold #e0e0e0]l[/] [#888]obs[/]",
                        "[bold #e0e0e0]x[/] [#888]hide[/]",
                    ]
                )
                if len(self._groups) > 1:
                    keys.append("[bold #e0e0e0]\\[\\][/] [#888]group[/]")
            if self._mode == "table":
                keys.append("[bold #e0e0e0]o[/] [#888]sort[/]")
            keys.extend(
                [
                    "[bold #e0e0e0]e[/] [#888]csv[/]",
                    "[bold #e0e0e0]y[/] [#888]python[/]",
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
        self.query_one("#query-summary").display = False
        self.query_one("#query-cursor").display = False

    def coach_flags(self) -> set[str]:
        """State the onboarding coach can check (``loaded``, ``chart``)."""
        flags = set()
        if self._loaded:
            flags.add("loaded")
            if self._mode == "chart":
                flags.add("chart")
        return flags

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
        self._busy = True
        status.update("Running /query…")
        try:
            results = await asyncio.to_thread(query.execute, self.client)
        except Exception as exc:
            self._busy = False
            status.update(f"Query failed: {exc}")
            self.notify(str(exc), severity="error")
            return
        self._busy = False
        self._records = [series_to_record(ts) for ts in results]
        self._groups = group_plot_records(self._records)
        self._loaded = True
        self._series_i = 0
        self._group_i = 0
        self._hidden.clear()
        self._cursor_i = -1
        self._mode = "chart" if len(self._groups) <= 1 else "table"
        line = coverage_status(query, self._records)
        status.update(line)
        if not self._records:
            self.notify("No series returned", severity="warning")
        elif "missing" in line:
            self.notify(line, severity="warning")
        self._fill_nav()
        self._show_selected()
        self.refresh_footer()

    def _fill_nav(self) -> None:
        nav = self.query_one("#query-series", ListView)
        nav.clear()
        group = self._selected_group()
        if self._mode == "chart" and group is not None:
            show = len(group.records) > 1
            nav.display = show
            if not show:
                return
            for record in group.records:
                hidden = str(record.get("id") or "") in self._hidden
                nav.append(LegendRow(record, hidden=hidden))
            ids = [str(r.get("id") or "") for r in group.records]
            current = self._selected_record()
            cid = str(current.get("id") or "") if current else ""
            nav.index = ids.index(cid) if cid in ids else 0
            return
        show = len(self._records) > 1
        nav.display = show
        if not show:
            return
        for record in self._records:
            nav.append(SeriesNavRow(record))
        nav.index = min(self._series_i, len(self._records) - 1)

    def _selected_record(self) -> Optional[dict[str, Any]]:
        if not self._records:
            return None
        return self._records[min(self._series_i, len(self._records) - 1)]

    def _selected_group(self) -> Optional[PlotGroup]:
        if not self._groups:
            return None
        return self._groups[min(self._group_i, len(self._groups) - 1)]

    def _visible_records(self) -> list[dict[str, Any]]:
        group = self._selected_group()
        if group is None:
            record = self._selected_record()
            return [record] if record else []
        return [
            rec
            for rec in group.records
            if str(rec.get("id") or "") not in self._hidden
        ]

    def _cursor_day(self) -> Optional[datetime]:
        dates = cursor_dates(self._visible_records())
        if not dates:
            return None
        if self._cursor_i < 0 or self._cursor_i >= len(dates):
            self._cursor_i = len(dates) - 1
        return dates[self._cursor_i]

    def _show_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            self.query_one("#query-summary").display = False
            self.query_one("#query-cursor").display = False
            self.query_one("#query-obs", ListView).clear()
            self.query_one("#query-chart", HistoryPlot).plot_records(None)
            self.query_one("#query-raw-body", Static).update("{}")
            self._apply_mode()
            return
        obs_list = self.query_one("#query-obs", ListView)
        obs_list.clear()
        rows = list(record.get("observations") or [])
        rows.sort(
            key=lambda obs: _parse_stamp(obs.get("observation_timestamp"))
            or datetime.min,
            reverse=self._newest_first,
        )
        for obs in rows:
            obs_list.append(ObservationRow(record, obs))
        self.query_one("#query-raw-body", Static).update(
            json.dumps(record, indent=2, default=str)
        )
        self._render_summary()
        self._render_chart()
        self._render_cursor()
        self._apply_mode()

    def _render_summary(self) -> None:
        record = self._selected_record()
        group = self._selected_group()
        panel = self.query_one("#query-summary")
        if record is None:
            panel.display = False
            return
        panel.display = True
        title = record_title(record)
        if self._mode == "chart" and group is not None and len(group.records) > 1:
            title = plot_group_title(group)
        self.query_one("#query-title", Label).update(title)
        self.query_one("#sum-kicker", Label).update(title.upper())
        last = record.get("last") or {}
        freq = record.get("frequency") or ""
        units = record.get("units") or ""
        period = format_period(last.get("observation_timestamp"), freq)
        self.query_one("#sum-latest-kicker", Label).update(
            f"Latest · {period}" if period != "—" else "Latest"
        )
        self.query_one("#sum-latest-value", Label).update(
            format_value(last.get("value"))
        )
        bits = [p for p in (units, freq) if p]
        self.query_one("#sum-units", Label).update(" · ".join(bits))
        span_records = (
            list(group.records)
            if self._mode == "chart" and group is not None
            else [record]
        )
        n = sum(int(r.get("n_observations") or 0) for r in span_records)
        self.query_one("#sum-cov-value", Label).update(coverage_years(span_records))
        self.query_one("#sum-cov-meta", Label).update(
            f"{n} observation" + ("" if n == 1 else "s")
        )
        released = format_release(last.get("release_timestamp"), short=True)
        sources = []
        for rec in span_records:
            src = str(rec.get("source") or "").strip()
            if src and src not in sources:
                sources.append(src)
        source = sources[0] if len(sources) == 1 else " · ".join(sources[:2])
        foot = []
        if released != "—":
            foot.append(f"Latest released {released}")
        if source:
            foot.append(f"Source: {source}")
        if self._mode == "chart" and group is not None and len(self._groups) > 1:
            foot.append(
                f"Other units not overlaid ({len(self._groups) - 1} group"
                + ("s" if len(self._groups) > 2 else "")
                + ")"
            )
        self.query_one("#sum-foot", Label).update(" · ".join(foot))

    def _render_chart(self) -> None:
        visible = self._visible_records()
        group = self._selected_group()
        colors = None
        if group is not None:
            colors = [
                CHART_PALETTE[group.records.index(rec) % len(CHART_PALETTE)]
                for rec in visible
                if rec in group.records
            ]
        self.query_one("#query-chart", HistoryPlot).plot_records(
            visible, cursor_date=self._cursor_day(), colors=colors
        )

    def _render_cursor(self) -> None:
        strip = self.query_one("#query-cursor")
        if self._mode != "chart" or not self._visible_records():
            strip.display = False
            return
        strip.display = True
        day = self._cursor_day()
        record = self._selected_record()
        freq = (record or {}).get("frequency") if record else None
        if day is None:
            self.query_one("#cur-period", Label).update("No observations")
            self.query_one("#cur-value", Label).update("—")
            self.query_one("#cur-meta", Label).update("")
            self.query_one("#cur-compare", Label).update("")
            return
        self.query_one("#cur-period", Label).update(format_period(day, freq))
        primary = None
        if record is not None:
            primary = observation_on_date(record, day)
        self.query_one("#cur-value", Label).update(
            format_value(primary.get("value") if primary else None)
        )
        self.query_one("#cur-meta", Label).update(
            f"Observed {format_release(day)}"
            + (
                f"  ·  Released {format_release(primary.get('release_timestamp'))}"
                if primary
                else "  ·  missing for selected series"
            )
        )
        bits = []
        for rec in self._visible_records():
            obs = observation_on_date(rec, day)
            bits.append(
                f"{series_line_label(rec)} {format_value(obs.get('value') if obs else None)}"
            )
        self.query_one("#cur-compare", Label).update("  ·  ".join(bits))

    def _apply_mode(self) -> None:
        table = self._mode == "table"
        chart = self._mode == "chart"
        raw = self._mode == "raw"
        self.query_one("#query-obs-header").display = table
        self.query_one("#query-obs").display = table
        self.query_one("#query-chart").display = chart
        self.query_one("#query-raw").display = raw
        self.query_one("#query-cursor").display = (
            chart and self._loaded and bool(self._visible_records())
        )
        order = "latest first" if self._newest_first else "oldest first"
        self.query_one("#query-obs-header", Label).update(
            f"{'Period':<14}{'Value':>14}{'Released':>16}     {order}"
        )
        if chart:
            group = self._selected_group()
            kicker = "CHART"
            if group is not None:
                extra = plot_group_label(group)
                n = len(self._visible_records())
                hidden = len(group.records) - n
                if n > 1:
                    extra += f"  ·  {n} series"
                if hidden > 0:
                    extra += f"  ·  {hidden} hidden"
                kicker = f"CHART     {extra}"
        else:
            kicker = {
                "table": f"OBSERVATIONS     {order}",
                "raw": "RAW JSON",
            }[self._mode]
        self.query_one("#query-pane-kicker", Label).update(kicker)
        if table:
            self.query_one("#query-obs", ListView).focus()
        elif chart and self.query_one("#query-series").display:
            self.query_one("#query-series", ListView).focus()
        elif len(self._records) > 1:
            self.query_one("#query-series", ListView).focus()

    def action_mode_table(self) -> None:
        self._mode = "table"
        self._fill_nav()
        self._show_selected()

    def action_mode_chart(self) -> None:
        self._mode = "chart"
        self._fill_nav()
        self._show_selected()

    def action_mode_raw(self) -> None:
        self._mode = "raw"
        self._fill_nav()
        self._show_selected()

    def action_obs_left(self) -> None:
        dates = cursor_dates(self._visible_records())
        if not dates:
            return
        if self._cursor_i < 0:
            self._cursor_i = len(dates) - 1
        self._cursor_i = max(0, self._cursor_i - 1)
        self._render_chart()
        self._render_cursor()

    def action_obs_right(self) -> None:
        dates = cursor_dates(self._visible_records())
        if not dates:
            return
        if self._cursor_i < 0:
            self._cursor_i = len(dates) - 1
        self._cursor_i = min(len(dates) - 1, self._cursor_i + 1)
        self._render_chart()
        self._render_cursor()

    def action_toggle_series(self) -> None:
        if self._mode != "chart":
            return
        record = self._selected_record()
        if record is None:
            return
        sid = str(record.get("id") or "")
        if not sid:
            return
        if sid in self._hidden:
            self._hidden.discard(sid)
        else:
            if len(self._visible_records()) <= 1:
                self.notify("Keep at least one series visible", severity="warning")
                return
            self._hidden.add(sid)
        self._fill_nav()
        self._show_selected()

    def action_toggle_sort(self) -> None:
        self._newest_first = not self._newest_first
        self._show_selected()

    def action_export_csv(self) -> None:
        if not self._records:
            self.notify("Run the query first", severity="warning")
            return
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        tag = f"q{self.query_id}" if self.query_id is not None else "workspace"
        path = EXPORT_DIR / f"{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path.write_text(records_to_csv(self._records), encoding="utf-8")
        self.notify(f"Wrote {path}")

    def action_copy_python(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        text = query_python_snippet(query)
        if copy_text(text):
            self.notify("Python snippet copied")
            return
        self.notify("Could not copy to clipboard", severity="warning")

    def action_prev_group(self) -> None:
        self._shift_group(-1)

    def action_next_group(self) -> None:
        self._shift_group(1)

    def _shift_group(self, delta: int) -> None:
        if len(self._groups) < 2:
            return
        self._group_i = (self._group_i + delta) % len(self._groups)
        group = self._selected_group()
        if group is not None:
            rec = group.records[0]
            try:
                self._series_i = self._records.index(rec)
            except ValueError:
                pass
        self._cursor_i = -1
        self._fill_nav()
        self._show_selected()

    def action_inspect(self) -> None:
        if self._mode != "table":
            record = self._selected_record()
            if self._mode == "chart" and record is not None:
                day = self._cursor_day()
                obs = observation_on_date(record, day) if day else None
                if obs:
                    self.app.push_screen(ObservationDetail(record, obs))
                    return
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
        loaded = {
            "collect",
            "inspect",
            "mode_table",
            "mode_chart",
            "mode_raw",
            "obs_left",
            "obs_right",
            "toggle_series",
            "toggle_sort",
            "export_csv",
            "copy_python",
            "prev_group",
            "next_group",
        }
        if action in loaded:
            return self._loaded and not self._busy
        return True

    @on(ListView.Highlighted, "#query-series")
    def on_series_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, PlotGroupRow):
            try:
                self._group_i = self._groups.index(event.item.group)
            except ValueError:
                return
            self._show_selected()
            return
        if isinstance(event.item, (SeriesNavRow, LegendRow)):
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
            ActionBinding("h / l", "obs", "Move the observation cursor (chart)"),
            ActionBinding("x", "toggle_series", "Hide or show a series on the chart"),
            ActionBinding("[ / ]", "group", "Cycle incompatible-unit chart groups"),
            ActionBinding("o", "sort", "Toggle observation table sort"),
            ActionBinding("e", "export_csv", "Write observations to CSV"),
            ActionBinding("y", "copy_python", "Copy a Python query snippet"),
            ActionBinding("j / k", "cursor", "Move series or observations"),
            ActionBinding("enter", "inspect", "Inspect the highlighted observation"),
            ActionBinding("space", "collect", "Collect metric/entity/series into workspace"),
        ),
        example="jst action add query --args '{\"query_id\":1}'",
    )
)
