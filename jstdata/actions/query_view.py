"""Query View — run a saved query and inspect observations.

Idle until ``r``. Results stay JSON records under the hood (one per series);
the human surface is a formatted table, optional chart, and a Raw JSON mode.
"""

from __future__ import annotations

from rich.markup import escape

import asyncio
import csv
import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from ..investigation.nav import annotations_panel
from ..investigation.query_edit import QueryEditor
from ..models import TimeSeries
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    register,
    stage_ids,
)
from .screen import BaseScreen

MODES = ("chart", "table", "sources", "edit")


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
    """Exact-ish table/detail number; raw precision stays on the record."""
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


def unit_scale(units: str | None) -> tuple[float, bool]:
    """Scale factor from reported units → display units, plus currency flag.

    Catalog values are often stored in thousands/millions of dollars. A raw
    ``14336729`` with units ``Thousands of Dollars`` is **$14.34B**, not $14.34M.
    """
    unit = (units or "").strip().lower()
    if not unit:
        return 1.0, False
    currency = any(
        tok in unit
        for tok in ("dollar", "usd", "$", "euro", "eur", "pound", "gbp", "yen")
    )
    # Explicit scale words (order matters: "thousands of millions" rare — skip).
    if "trillion" in unit:
        return 1_000_000_000_000.0, currency
    if "billion" in unit:
        return 1_000_000_000.0, currency
    if "million" in unit:
        return 1_000_000.0, currency
    if "thousand" in unit:
        return 1_000.0, currency
    return 1.0, currency


def scaled_number(value: Any, *, units: str | None = None) -> Optional[float]:
    """Apply unit scale; None when value is missing/non-numeric."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    scale, _ = unit_scale(units)
    return number * scale


def format_compact(value: Any, *, units: str | None = None) -> str:
    """Headline / chart-axis number: $14.34B, 4.3%, 71.5, … (unit-aware)."""
    unit = (units or "").strip().lower()
    # Percents / rates / ratios: no magnitude scaling.
    if any(tok in unit for tok in ("percent", "%", "ratio", "index")) and "dollar" not in unit:
        if value is None:
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number != number:
            return "—"
        sign = "-" if number < 0 else ""
        body = f"{abs(number):.2f}".rstrip("0").rstrip(".")
        suffix = "%" if "percent" in unit or unit == "%" else ""
        return f"{sign}{body}{suffix}"

    scaled = scaled_number(value, units=units)
    if scaled is None:
        return "—" if value is None else str(value)
    _, currency = unit_scale(units)
    sign = "-" if scaled < 0 else ""
    mag = abs(scaled)
    prefix = "$" if currency else ""
    if mag >= 1_000_000_000_000:
        body = f"{mag / 1_000_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}T"
    if mag >= 1_000_000_000:
        body = f"{mag / 1_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}B"
    if mag >= 1_000_000:
        body = f"{mag / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}M"
    if mag >= 10_000:
        body = f"{mag / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}K"
    body = format_value(mag)
    return f"{sign}{prefix}{body}"


def format_exact(value: Any, *, units: str | None = None) -> str:
    """Full-precision display after unit scaling (observation strip / table)."""
    unit = (units or "").strip().lower()
    if any(tok in unit for tok in ("percent", "%", "ratio", "index")) and "dollar" not in unit:
        return format_value(value)
    scaled = scaled_number(value, units=units)
    if scaled is None:
        return format_value(value)
    _, currency = unit_scale(units)
    formatted = format_value(scaled, digits=0 if abs(scaled) >= 1000 else 2)
    return f"${formatted}" if currency else formatted


def format_pct_change(current: Any, prior: Any) -> str:
    """Signed percent change for headlines (e.g. ``+3.3%``)."""
    try:
        cur = float(current)
        old = float(prior)
    except (TypeError, ValueError):
        return "—"
    if old == 0 or cur != cur or old != old:
        return "—"
    pct = (cur - old) / abs(old) * 100.0
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _obs_chronological(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = list(record.get("observations") or [])
    rows.sort(
        key=lambda obs: _parse_stamp(obs.get("observation_timestamp"))
        or datetime.min
    )
    return rows


def headline_changes(
    record: dict[str, Any],
) -> tuple[str, str]:
    """Return ``(pop_label, yoy_label)`` for the selected series."""
    rows = _obs_chronological(record)
    if len(rows) < 2:
        return "—", "—"
    latest = rows[-1]
    prev = rows[-2]
    freq = str(record.get("frequency") or "").lower()
    pop_tag = "QoQ" if "quarter" in freq else ("MoM" if "month" in freq else "PoP")
    pop = format_pct_change(latest.get("value"), prev.get("value"))
    pop_label = f"{pop} {pop_tag}" if pop != "—" else "—"

    latest_dt = _parse_stamp(latest.get("observation_timestamp"))
    yoy = "—"
    if latest_dt is not None:
        target_year = latest_dt.year - 1
        # Prefer same month/quarter in prior year; else nearest earlier stamp ≥11mo.
        candidates = [
            obs
            for obs in rows[:-1]
            if (dt := _parse_stamp(obs.get("observation_timestamp"))) is not None
            and dt.year == target_year
            and dt.month == latest_dt.month
        ]
        if not candidates:
            floor = latest_dt - timedelta(days=400)
            ceiling = latest_dt - timedelta(days=300)
            candidates = [
                obs
                for obs in rows[:-1]
                if (dt := _parse_stamp(obs.get("observation_timestamp"))) is not None
                and floor <= dt <= ceiling
            ]
        if candidates:
            yoy = format_pct_change(latest.get("value"), candidates[-1].get("value"))
    yoy_label = f"{yoy} YoY" if yoy != "—" else "—"
    return pop_label, yoy_label


def format_compact_scaled(scaled_value: float, *, units: str | None = None) -> str:
    """Format a value that is **already** unit-scaled (chart ticks)."""
    _, currency = unit_scale(units)
    unit = (units or "").strip().lower()
    if any(tok in unit for tok in ("percent", "%", "ratio", "index")) and "dollar" not in unit:
        sign = "-" if scaled_value < 0 else ""
        body = f"{abs(scaled_value):.2f}".rstrip("0").rstrip(".")
        suffix = "%" if "percent" in unit or unit == "%" else ""
        return f"{sign}{body}{suffix}"
    sign = "-" if scaled_value < 0 else ""
    mag = abs(scaled_value)
    prefix = "$" if currency else ""
    if mag >= 1_000_000_000_000:
        body = f"{mag / 1_000_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}T"
    if mag >= 1_000_000_000:
        body = f"{mag / 1_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}B"
    if mag >= 1_000_000:
        body = f"{mag / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}M"
    if mag >= 10_000:
        body = f"{mag / 1_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}K"
    return f"{sign}{prefix}{format_value(mag)}"


def _compact_yticks(
    values: Sequence[float], *, units: str | None = None
) -> tuple[list[float], list[str]]:
    if not values:
        return [], []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [lo], [format_compact_scaled(lo, units=units)]
    span = hi - lo
    step = span / 4.0
    ticks = [lo + step * i for i in range(5)]
    return ticks, [format_compact_scaled(t, units=units) for t in ticks]


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
    all_values: list[float] = []
    cursor_point: tuple[str, float] | None = None
    names = line_labels(recs)
    for i, (rec, name) in enumerate(zip(recs, names)):
        times, values = history_points(rec)
        if not times:
            continue
        if not units:
            units = str(rec.get("units") or "").strip()
        scale, _ = unit_scale(units)
        # Skip scaling for percent-like units (handled as identity in unit_scale).
        unit_l = units.lower()
        if any(tok in unit_l for tok in ("percent", "%", "ratio", "index")) and "dollar" not in unit_l:
            scale = 1.0
        scaled = [v * scale for v in values]
        all_values.extend(scaled)
        plt.date_form(CHART_DATE_FORM)
        draw = plt.scatter if len(scaled) == 1 else plt.plot
        if colors and i < len(colors):
            color = colors[i]
        elif len(recs) == 1:
            color = CHART_LINE_COLOR
        else:
            color = CHART_PALETTE[i % len(CHART_PALETTE)]
        draw(times, scaled, marker="braille", label=name, color=color)
        drawn += 1
        if cursor_date is not None and cursor_point is None:
            target = _naive(cursor_date).strftime("%Y-%m-%d")
            for t, v in zip(times, scaled):
                if t == target:
                    cursor_point = (t, v)
                    break
    if not drawn:
        plt.title("No observations")
        return False
    y_ticks, y_labels = _compact_yticks(all_values, units=units)
    if y_ticks:
        try:
            plt.yticks(y_ticks, y_labels)
        except Exception:  # noqa: BLE001
            pass
    # Axis label: prefer currency when we scaled thousands/millions of dollars.
    _, currency = unit_scale(units)
    if currency and unit_scale(units)[0] != 1.0:
        plt.ylabel("USD")
    else:
        plt.ylabel(units)
    ticks, labels = calendar_xticks(recs)
    if ticks:
        plt.xticks(ticks, labels)
    if cursor_date is not None:
        day = _naive(cursor_date).strftime("%Y-%m-%d")
        try:
            plt.vline(day)
        except Exception:  # noqa: BLE001
            pass
        # Bright marker so h/l movement is visible on the series, not just a line.
        if cursor_point is not None:
            try:
                plt.scatter(
                    [cursor_point[0]],
                    [cursor_point[1]],
                    marker="hd",
                    color=(250, 250, 250),
                )
            except Exception:  # noqa: BLE001
                pass
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
        units = self.record.get("units")
        period = format_period(self.obs.get("observation_timestamp"), freq)
        raw = self.obs.get("value")
        value = format_exact(raw, units=units)
        released = format_release(self.obs.get("release_timestamp"))
        classes = "obs-row obs-missing" if raw is None else "obs-row"
        yield Label(
            f"{period:<14}{value:>18}{released:>16}",
            classes=classes,
        )


class SeriesNavRow(ListItem):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        last = self.record.get("last") or {}
        entities = self.record.get("entities") or []
        name = (
            entities[0]["label"]
            if entities
            else (self.record.get("label") or self.record.get("id"))
        )
        value = format_value(last.get("value"))
        freq = self.record.get("frequency") or ""
        units = self.record.get("units") or ""
        yield Label(str(name), classes="series-nav-name")
        meta = " · ".join(p for p in (value, freq, units) if p)
        yield Label(meta or "—", classes="series-nav-meta")


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
        Binding("a", "annotate", "Annotate", show=False),
        Binding("t", "mode_table", "Table", show=False),
        Binding("c", "mode_chart", "Chart", show=False),
        Binding("v", "mode_sources", "Sources", show=False),
        Binding("d", "mode_sources", "Sources", show=False),
        Binding("e", "mode_edit", "Edit", show=False),
        Binding("plus", "add_compare", "Add entity", show=False),
        Binding("=", "add_compare", "Add entity", show=False),
        Binding("m", "add_metric", "Add metric", show=False),
        Binding("space", "collect", "Collect into workspace", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("h", "obs_left", "Older observation", show=False),
        Binding("l", "obs_right", "Newer observation", show=False),
        Binding("left", "obs_left", "Older observation", show=False),
        Binding("right", "obs_right", "Newer observation", show=False),
        Binding("x", "toggle_series", "Hide or show series", show=False),
        Binding("o", "toggle_sort", "Sort observations", show=False),
        Binding("E", "export_csv", "Export CSV", show=False),
        Binding("y", "copy_python", "Copy Python", show=False),
        Binding("[", "prev_group", "Previous chart group", show=False),
        Binding("]", "next_group", "Next chart group", show=False),
        Binding("enter", "inspect", "Inspect", priority=True),
    ]

    FOOTER_CHROME: tuple[str, ...] = BaseScreen.FOOTER_CHROME

    DEFAULT_CSS = """
    #query-body { height: 1fr; padding: 0 2 0 2; }
    #query-title { color: #f5f5f5; text-style: bold; height: 1; margin-top: 1; }
    #query-subtitle { color: #a0a0a0; height: 1; }
    #query-layers {
        color: #666;
        height: 1;
        margin: 1 0;
    }
    #query-headline {
        height: 2;
        margin-bottom: 0;
        display: none;
    }
    #hl-values { color: #f5f5f5; text-style: bold; height: 1; }
    #hl-meta { color: #888; height: 1; }
    #query-status { color: #666; height: 1; display: none; }
    #query-main { height: 1fr; min-height: 12; }
    #query-series {
        width: 28;
        height: 1fr;
        background: #0f0f0f;
        display: none;
        border-right: solid #222;
    }
    #query-pane { width: 1fr; height: 1fr; }
    #query-obs-header { color: #888; height: 1; }
    #query-obs { height: 1fr; background: #0a0a0a; }
    #query-chart {
        height: 1fr;
        width: 1fr;
        min-height: 14;
        display: none;
    }
    #query-obs-strip {
        height: 2;
        margin-top: 0;
        display: none;
    }
    #strip-line1 { color: #e0e0e0; height: 1; }
    #strip-line2 { color: #888; height: 1; }
    #query-annotations {
        color: #c0c0c0;
        height: 5;
        min-height: 4;
        max-height: 8;
        margin-top: 1;
        border-top: solid #222;
        padding-top: 0;
        overflow-y: auto;
    }
    #query-raw {
        height: 1fr;
        color: #c0c0c0;
        display: none;
    }
    #query-edit { height: 1fr; width: 1fr; display: none; }
    .obs-row { width: 100%; height: 1; color: #e0e0e0; }
    .obs-missing { color: #666; }
    .series-nav-name {
        width: 100%;
        height: 1;
        color: #f0f0f0;
        text-style: bold;
    }
    .series-nav-meta {
        width: 100%;
        height: 1;
        color: #888;
    }
    .series-nav-row { width: 100%; height: 1; color: #d0d0d0; }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    ObservationRow {
        height: 2;
        min-height: 2;
        max-height: 2;
        border-left: solid #0a0a0a;
        padding: 0 1;
    }
    SeriesNavRow {
        height: 3;
        min-height: 3;
        max-height: 3;
        border-left: solid #0a0a0a;
        padding: 0 1;
    }
    PlotGroupRow, LegendRow {
        height: 2;
        min-height: 2;
        max-height: 2;
        border-left: solid #0a0a0a;
        padding: 0 1;
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
        self._mode = "chart"
        self._series_i = 0
        self._group_i = 0
        self._hidden: set[str] = set()
        self._cursor_i = -1
        self._newest_first = True
        self._busy = False

    def compose_body(self) -> ComposeResult:
        with Vertical(id="query-body"):
            yield Label("", id="query-title")
            yield Label("", id="query-subtitle")
            yield Static("", id="query-layers", markup=True)
            with Vertical(id="query-headline"):
                yield Label("", id="hl-values")
                yield Label("", id="hl-meta")
            yield Static("", id="query-status", markup=True)
            with Horizontal(id="query-main"):
                yield ListView(id="query-series")
                with Vertical(id="query-pane"):
                    yield Label(
                        f"{'Period':<14}{'Value':>14}{'Released':>16}",
                        id="query-obs-header",
                    )
                    yield ListView(id="query-obs")
                    yield HistoryPlot(id="query-chart")
                    with VerticalScroll(id="query-raw"):
                        yield Static("", id="query-raw-body")
                    yield QueryEditor(self.client, id="query-edit")
            with Vertical(id="query-obs-strip"):
                yield Label("", id="strip-line1")
                yield Label("", id="strip-line2")
            yield Static("", id="query-annotations", markup=True)

    def on_mount(self) -> None:
        super().on_mount()
        self._render_idle()
        self._apply_mode()
        self.query_one("#query-obs", ListView).focus()
        # Chart-first: auto-run when the query has something to execute.
        try:
            query = self._resolve_query()
            if not query.is_empty():
                self.call_later(self.action_load)
        except Exception:  # noqa: BLE001
            pass

    def footer_status(self) -> list[str]:
        return []

    def footer_action_keys(self) -> list[str]:
        if self._mode == "edit":
            return [
                "[bold #e0e0e0]ctrl+s[/] [#888]update[/]",
                "[bold #e0e0e0]ctrl+n[/] [#888]save as new[/]",
                "[bold #e0e0e0]esc[/] [#888]cancel[/]",
            ]
        keys = [
            "[bold #e0e0e0]c[/]/[bold #e0e0e0]t[/]/[bold #e0e0e0]v[/]/[bold #e0e0e0]e[/] [#888]views[/]",
            "[bold #e0e0e0]a[/] [#888]annotate[/]",
        ]
        if self._loaded:
            if self._mode == "chart":
                keys.extend(
                    [
                        "[bold #e0e0e0]h[/]/[bold #e0e0e0]l[/] [#888]period[/]",
                        "[bold #e0e0e0]+[/] [#888]entity[/]",
                        "[bold #e0e0e0]m[/] [#888]metric[/]",
                    ]
                )
            if self._mode == "table":
                keys.append("[bold #e0e0e0]o[/] [#888]sort[/]")
        return keys

    def shell_breadcrumb(self) -> str:
        try:
            query = self._resolve_query()
        except Exception:
            return "Query"
        tag = f"Q{query.id}" if query.id else "Draft"
        name = query.name or tag
        mode = {
            "chart": "Chart",
            "table": "Table",
            "sources": "Sources",
            "edit": "Edit",
        }.get(self._mode, "Chart")
        return f"{tag} · {name} · {mode}"

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
            self.query_one("#query-title", Label).update(str(exc))
            return
        tag = f"Q{query.id}" if query.id else "Draft"
        if query.name:
            title = f"{tag} · {escape(query.name)}"
        else:
            title = tag
        self.query_one("#query-title", Label).update(title)
        # Spec line is Sources material — keep subtitle empty until load.
        self.query_one("#query-subtitle", Label).update("")
        self._render_annotations(query)
        self.query_one("#query-headline").display = False
        self.query_one("#query-obs-strip").display = False

    def _render_annotations(self, query: Query) -> None:
        panel = self.query_one("#query-annotations", Static)
        lines: list[str] = ["[dim]── Research notes ──[/]"]
        if query.annotations:
            lines.append(escape(query.annotations))
        for i, note in enumerate(query.notes_on or [], start=1):
            span = ""
            if note.from_ or note.to:
                span = f" · {note.from_ or '…'} – {note.to or '…'}"
            series = f" · {note.series}" if note.series else ""
            lines.append(
                f"[bold]{i:02d}[/] {escape(note.text)}[dim]{series}{span}[/]"
            )
        if len(lines) == 1:
            body = annotations_panel(query.annotations) if query.id else ""
            if body:
                lines.append(body)
            else:
                lines.append("[dim]No annotations yet. Press a to annotate.[/]")
        panel.update("\n".join(lines))
        panel.display = self._mode not in {"sources", "edit"}

    def refresh_annotations(self) -> None:
        """Host hook after annotation edits."""
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._render_annotations(query)

    def action_annotate(self) -> None:
        if self.query_id is None:
            self.notify(
                "Save as a query first to attach annotations",
                severity="warning",
            )
            return
        edit = getattr(self.app, "edit_query_annotations", None)
        if callable(edit):
            edit(self.query_id)

    def coach_flags(self) -> set[str]:
        """State the onboarding coach can check (``loaded``, ``chart``)."""
        flags = set()
        if self._loaded:
            flags.add("loaded")
            if self._mode == "chart":
                flags.add("chart")
        return flags

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._mode == "edit":
            # Chart/table navigation keys belong to the editor while editing.
            if action in {
                "inspect",
                "cursor_down",
                "cursor_up",
                "obs_left",
                "obs_right",
                "toggle_series",
                "toggle_sort",
                "add_compare",
                "add_metric",
                "collect",
                "load",
                "annotate",
                "export_csv",
                "copy_python",
                "prev_group",
                "next_group",
            }:
                return False
        return True

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
        self._mode = "chart"
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
            self.query_one("#query-headline").display = False
            self.query_one("#query-obs-strip").display = False
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
        self._render_headline()
        self._render_chart()
        self._render_obs_strip()
        self._render_sources_body()
        self._apply_mode()

    def _query_title_line(self, query: Query, record: Optional[dict[str, Any]]) -> str:
        tag = f"Q{query.id}" if query.id else "Draft"
        if query.name:
            return f"{tag} · {escape(query.name)}"
        if record is not None:
            return f"{tag} · {escape(record_title(record))}"
        return tag

    def _context_subtitle(self, record: Optional[dict[str, Any]]) -> str:
        """Entity · frequency — not a repeat of the query/metric title."""
        if record is None:
            return ""
        entities = record.get("entities") or []
        entity = ""
        if entities:
            entity = str(entities[0].get("label") or entities[0].get("id") or "")
        freq = str(record.get("frequency") or "")
        bits = [b for b in (entity, freq) if b]
        return " · ".join(bits)

    def _render_headline(self) -> None:
        record = self._selected_record()
        panel = self.query_one("#query-headline")
        try:
            query = self._resolve_query()
        except Exception:
            query = None
        if record is None or query is None:
            panel.display = False
            return
        panel.display = self._mode == "chart"
        self.query_one("#query-title", Label).update(
            self._query_title_line(query, record)
        )
        self.query_one("#query-subtitle", Label).update(
            self._context_subtitle(record)
        )
        last = record.get("last") or {}
        units = str(record.get("units") or "")
        freq = str(record.get("frequency") or "")
        period = format_period(last.get("observation_timestamp"), freq)
        value = format_compact(last.get("value"), units=units)
        pop, yoy = headline_changes(record)
        self.query_one("#hl-values", Label).update(
            f"{value}     {pop}     {yoy}"
        )
        # Period only under the headline — units live in Sources / strip.
        self.query_one("#hl-meta", Label).update(period)

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

    def _render_obs_strip(self) -> None:
        strip = self.query_one("#query-obs-strip")
        if self._mode != "chart" or not self._visible_records():
            strip.display = False
            return
        strip.display = True
        day = self._cursor_day()
        record = self._selected_record()
        freq = (record or {}).get("frequency") if record else None
        units = (record or {}).get("units") if record else None
        if day is None:
            self.query_one("#strip-line1", Label).update("No observations")
            self.query_one("#strip-line2", Label).update("")
            return
        primary = (
            observation_on_date(record, day) if record is not None else None
        )
        exact = format_exact(
            primary.get("value") if primary else None, units=units
        )
        self.query_one("#strip-line1", Label).update(
            f"◀ {format_period(day, freq)} ▶     {exact}"
        )
        meta = f"Observed {format_release(day)}"
        if primary:
            meta += (
                f" · Reported "
                f"{format_release(primary.get('release_timestamp'))}"
            )
        else:
            meta += " · missing for selected series"
        self.query_one("#strip-line2", Label).update(meta)

    def _render_sources_body(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.query_one("#query-raw-body", Static).update(str(exc))
            return
        record = self._selected_record()
        coverage = (
            coverage_status(query, self._records) if self._records else "Not run yet"
        )
        lines = [
            "── Query details ──",
            query_spec_line(query),
            coverage,
            f"Span: {coverage_years(self._records)}",
            "",
            "── Spec ──",
            json.dumps(query.to_dict(), indent=2, default=str),
        ]
        if record is not None:
            lines.extend(
                [
                    "",
                    "── Selected series ──",
                    json.dumps(
                        {
                            k: record.get(k)
                            for k in (
                                "id",
                                "label",
                                "metric_id",
                                "entities",
                                "frequency",
                                "units",
                                "source",
                                "n_observations",
                            )
                        },
                        indent=2,
                        default=str,
                    ),
                ]
            )
        self.query_one("#query-raw-body", Static).update("\n".join(lines))

    def _apply_mode(self) -> None:
        table = self._mode == "table"
        chart = self._mode == "chart"
        sources = self._mode == "sources"
        edit = self._mode == "edit"
        self.query_one("#query-obs-header").display = table
        self.query_one("#query-obs").display = table
        self.query_one("#query-chart").display = chart
        self.query_one("#query-raw").display = sources
        editor = self.query_one("#query-edit", QueryEditor)
        if edit:
            editor.display = True
            editor.add_class("-visible")
        else:
            if editor.display or editor.has_class("-visible"):
                editor.hide_editor()
        self.query_one("#query-headline").display = (
            chart and self._loaded and self._selected_record() is not None
        )
        self.query_one("#query-obs-strip").display = (
            chart and self._loaded and bool(self._visible_records())
        )
        try:
            self.query_one("#query-annotations").display = not sources and not edit
        except Exception:  # noqa: BLE001
            pass
        if edit:
            self.query_one("#query-series").display = False
            self.query_one("#query-title").display = False
            self.query_one("#query-subtitle").display = False
        else:
            self.query_one("#query-title").display = True
            self.query_one("#query-subtitle").display = True
        # Tab strip — Chart / Table / Sources / Edit
        def _tab(active: str) -> str:
            parts = []
            for key, label in (
                ("chart", "Chart"),
                ("table", "Table"),
                ("sources", "Sources"),
                ("edit", "Edit"),
            ):
                if key == active:
                    parts.append(f"[bold #86efac on #163324] {label} [/]")
                else:
                    parts.append(f"[#888]{label}[/]")
            return "  ".join(parts)

        try:
            self.query_one("#query-layers", Static).update(_tab(self._mode))
        except Exception:  # noqa: BLE001
            pass
        order = "latest first" if self._newest_first else "oldest first"
        self.query_one("#query-obs-header", Label).update(
            f"{'Period':<14}{'Value':>14}{'Released':>16}     {order}"
        )
        if edit:
            pass  # focus set by show_editor
        elif table:
            self.query_one("#query-obs", ListView).focus()
        elif chart and self.query_one("#query-series").display:
            self.query_one("#query-series", ListView).focus()
        elif len(self._records) > 1 and not sources:
            self.query_one("#query-series", ListView).focus()
        try:
            refresh = getattr(self.app, "refresh_shell_chrome", None)
            if callable(refresh):
                refresh(self)
        except Exception:  # noqa: BLE001
            pass
        self.refresh_footer()

    def action_mode_table(self) -> None:
        self._mode = "table"
        self._fill_nav()
        self._show_selected()

    def action_mode_chart(self) -> None:
        self._mode = "chart"
        self._fill_nav()
        self._show_selected()

    def action_mode_sources(self) -> None:
        self._mode = "sources"
        self._fill_nav()
        self._show_selected()

    def action_mode_edit(self) -> None:
        """Enter the in-pane query editor (fourth view)."""
        if self.query_id is None:
            self.notify("Save as a query first to edit it", severity="warning")
            return
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        inv = getattr(self.app, "investigation", None)
        if inv is None:
            self.notify("No investigation loaded", severity="error")
            return
        editor = self.query_one("#query-edit", QueryEditor)
        active = getattr(self.app, "active", None)
        editor.show_editor(
            query,
            investigation=inv,
            app_dir=getattr(self.app, "app_dir", None),
            project_id=getattr(self.app, "project_id", None),
            investigation_id=getattr(active, "investigation", None) if active else None,
            labels=getattr(self.app, "labels", None) or {},
        )
        self._mode = "edit"
        self._apply_mode()

    def action_mode_raw(self) -> None:
        self.action_mode_sources()

    def action_mode_definition(self) -> None:
        self.action_mode_sources()

    @on(QueryEditor.Applied)
    def _editor_applied(self, event: QueryEditor.Applied) -> None:
        event.stop()
        qid = event.query_id
        mode = event.mode
        self.query_one("#query-edit", QueryEditor).hide_editor()
        open_fn = getattr(self.app, "open_query_view", None)
        if mode == "new" and callable(open_fn):
            open_fn(qid)
        else:
            self.query_id = qid
            self._mode = "chart"
            self._loaded = False
            self._apply_mode()
            self._render_idle()
            self.call_later(self.action_load)
        refresh = getattr(self.app, "refresh_shell_chrome", None)
        if callable(refresh):
            refresh()
        self.notify("Query updated" if mode == "update" else f"Saved as Q{qid}")

    @on(QueryEditor.Cancelled)
    def _editor_cancelled(self, event: QueryEditor.Cancelled) -> None:
        event.stop()
        self.query_one("#query-edit", QueryEditor).hide_editor()
        self._mode = "chart"
        self._fill_nav()
        self._show_selected()
        self._apply_mode()

    def _append_query_resource(self, kind: str, result: dict) -> None:
        """Persist a picked entity/metric onto the active query and re-run."""
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        rid = str(result.get("id") or "")
        if not rid:
            return
        bag = query.entity if kind == "entity" else query.metric
        if rid in bag:
            self.notify(f"{rid} already in query", severity="warning")
            return
        if kind == "entity":
            query.entity = list(query.entity) + [rid]
        else:
            query.metric = list(query.metric) + [rid]
        from ..investigation.api import persist_investigation
        from ..investigation.naming import suggest_query_name

        labels = getattr(self.app, "labels", {}) or {}
        if result.get("label"):
            labels[rid] = str(result["label"])
            remember = getattr(self.app, "remember_label", None)
            if callable(remember):
                remember(rid, str(result["label"]))
        query.name = suggest_query_name(query.metric, query.entity, labels=labels)
        persist_investigation(
            getattr(self.app, "investigation"),
            app_dir=getattr(self.app, "app_dir", None),
        )
        self.notify(f"Added {labels.get(rid, rid)} — re-running")
        self.action_load()
        refresh = getattr(self.app, "refresh_shell_chrome", None)
        if callable(refresh):
            refresh()

    def _open_scoped_picker(self, kind: str) -> None:
        """Pick another entity (scoped to first metric) or metric (first entity)."""
        if self.query_id is None:
            self.notify("Open a draft query first", severity="warning")
            return
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        if kind == "entity" and not query.metric:
            self.notify("Add a metric first, then compare entities", severity="warning")
            return
        if kind == "metric" and not query.entity:
            self.notify("Add an entity first, then add metrics", severity="warning")
            return

        from ..investigation.naming import label_for
        from ..investigation.resource_picker import ResourcePickerModal

        labels = getattr(self.app, "labels", {}) or {}
        seed_metric = query.metric[0] if query.metric else None
        seed_entity = query.entity[0] if query.entity else None
        scope_label = (
            label_for(labels, seed_metric)
            if kind == "entity" and seed_metric
            else label_for(labels, seed_entity)
            if kind == "metric" and seed_entity
            else None
        )
        exclude = list(query.entity if kind == "entity" else query.metric)

        def _done(result: object | None) -> None:
            if isinstance(result, dict):
                self._append_query_resource(kind, result)

        client = getattr(self.app, "client", None)
        if client is None:
            return
        active = getattr(self.app, "active", None)
        self.app.push_screen(
            ResourcePickerModal(
                client,
                kind,  # type: ignore[arg-type]
                app_dir=getattr(self.app, "app_dir", None),
                project_id=getattr(self.app, "project_id", None),
                investigation_id=(
                    getattr(active, "investigation", None) if active else None
                ),
                scope_metric=seed_metric if kind == "entity" else None,
                scope_entity=seed_entity if kind == "metric" else None,
                scope_label=scope_label,
                exclude_ids=exclude,
            ),
            _done,
        )

    def action_add_compare(self) -> None:
        """Append an entity that carries the query's first metric, then re-run."""
        self._open_scoped_picker("entity")

    def action_add_metric(self) -> None:
        """Append a metric available on the query's first entity, then re-run."""
        self._open_scoped_picker("metric")

    def action_obs_left(self) -> None:
        dates = cursor_dates(self._visible_records())
        if not dates:
            return
        if self._cursor_i < 0:
            self._cursor_i = len(dates) - 1
        self._cursor_i = max(0, self._cursor_i - 1)
        self._render_chart()
        self._render_obs_strip()

    def action_obs_right(self) -> None:
        dates = cursor_dates(self._visible_records())
        if not dates:
            return
        if self._cursor_i < 0:
            self._cursor_i = len(dates) - 1
        self._cursor_i = min(len(dates) - 1, self._cursor_i + 1)
        self._render_chart()
        self._render_obs_strip()

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
            "mode_sources",
            "mode_raw",
            "add_compare",
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
        return super().check_action(action, parameters)

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
            "Chart-first observation workbench for a draft or saved query. "
            "Auto-runs when opened; c/t/v/e switch Chart/Table/Sources/Edit; "
            "+ adds a comparison entity."
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
            ActionBinding("r", "load", "Re-run GET /query"),
            ActionBinding("a", "annotate", "Edit query annotations"),
            ActionBinding("c / t / v / e", "mode", "Chart, table, sources, or edit"),
            ActionBinding("+", "add_compare", "Add entity on first metric and re-run"),
            ActionBinding("m", "add_metric", "Add metric on first entity and re-run"),
            ActionBinding("h / l", "obs", "Move the observation cursor (chart)"),
            ActionBinding("x", "toggle_series", "Hide or show a series on the chart"),
            ActionBinding("[ / ]", "group", "Cycle incompatible-unit chart groups"),
            ActionBinding("o", "sort", "Toggle observation table sort"),
            ActionBinding("E", "export_csv", "Write observations to CSV"),
            ActionBinding("y", "copy_python", "Copy a Python query snippet"),
            ActionBinding("j / k", "cursor", "Move series or observations"),
            ActionBinding("enter", "inspect", "Inspect the highlighted observation"),
            ActionBinding("space", "collect", "Collect metric/entity/series into workspace"),
        ),
        example=':add query --args \'{"query_id":1}\'',
    )
)
