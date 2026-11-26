"""Query View — run a saved query and inspect observations.

Idle until ``r``. Results stay JSON records under the hood (one per series);
the human surface is a chart or observation table above research notes.
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
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label, ListItem, ListView, Static, TextArea
from textual_plotext import PlotextPlot

from ..client import JSTDataClient
from ..investigation.model import Query, QueryRemark, Workspace
from ..investigation.naming import label_for
from ..investigation.query_compose import (
    OBS_MODES,
    QueryComposeHeader,
    frequency_options_for,
    normalize_variant_frequency,
)
from ..investigation.ui_primitives import ActionButton
from ..models import TimeSeries
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    register,
    stage_ids,
)
from .screen import BaseScreen

MODES = ("chart", "table")


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
        body = f"{mag / 1_000_000_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}T"
    if mag >= 1_000_000_000:
        body = f"{mag / 1_000_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}B"
    if mag >= 1_000_000:
        body = f"{mag / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{prefix}{body}M"
    if mag >= 10_000:
        body = f"{mag / 1_000:.1f}".rstrip("0").rstrip(".")
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
        return dt.strftime("%b %Y")
    return dt.strftime("%Y-%m-%d")


def format_release(stamp: Any, *, short: bool = False, human: bool = False) -> str:
    dt = _parse_stamp(stamp)
    if dt is None:
        return "—"
    if human:
        return f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    if short:
        return dt.strftime("%b %Y")
    return dt.strftime("%Y-%m-%d")


def _expected_gap_days(frequency: Optional[str]) -> Optional[float]:
    """Max days between consecutive observations before treating as a gap."""
    freq = (frequency or "").lower()
    if "day" in freq:
        return 2.5
    if "week" in freq:
        return 10.0
    if "month" in freq:
        return 45.0
    if "quarter" in freq:
        return 120.0
    if "annual" in freq or freq in {"yearly", "year"}:
        return 400.0
    return None


def history_segments(
    record: dict[str, Any],
) -> list[tuple[list[str], list[float]]]:
    """Split a series into contiguous segments; gaps are not connected."""
    times, values = history_points(record)
    if not times:
        return []
    gap = _expected_gap_days(str(record.get("frequency") or "") or None)
    if gap is None or len(times) == 1:
        return [(times, values)]
    segments: list[tuple[list[str], list[float]]] = []
    cur_t = [times[0]]
    cur_v = [values[0]]
    for i in range(1, len(times)):
        prev = datetime.strptime(times[i - 1], "%Y-%m-%d")
        cur = datetime.strptime(times[i], "%Y-%m-%d")
        if (cur - prev).days > gap:
            segments.append((cur_t, cur_v))
            cur_t = [times[i]]
            cur_v = [values[i]]
        else:
            cur_t.append(times[i])
            cur_v.append(values[i])
    segments.append((cur_t, cur_v))
    return segments


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
    """Loading/result line: how many requested series came back."""
    n = len(records)
    expected = query.expected_pairs() if hasattr(query, "expected_pairs") else []
    if not expected:
        return f"{n} series available" if n else "No series available"
    got: set[tuple[str, str]] = set()
    for rec in records:
        metric = str(rec.get("metric_id") or "")
        for ent in rec.get("entities") or []:
            eid = str(ent.get("id") or "")
            if metric and eid:
                got.add((metric, eid))
    available = len(expected) - sum(1 for pair in expected if pair not in got)
    missing = [pair for pair in expected if pair not in got]
    requested = len(expected)
    if not n:
        return f"0 of {requested} requested series available"
    if missing:
        sample = ", ".join(f"{m} × {e}" for m, e in missing[:2])
        extra = f" +{len(missing) - 2}" if len(missing) > 2 else ""
        return (
            f"{available} of {requested} requested series available"
            f" · missing {sample}{extra}"
        )
    return f"{n} of {requested} requested series available"


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
    bound_lo: Optional[datetime] = None,
    bound_hi: Optional[datetime] = None,
    colors: Optional[Sequence[tuple[int, int, int]]] = None,
) -> bool:
    """Draw one or more series onto a plotext figure.

    Overlay series that share an axis (same units). Returns True when at
    least one line (or scatter) was drawn. X ticks are calendar years when
    the span is long enough. ``bound_lo`` / ``bound_hi`` grey out the
    outside; ``cursor_date`` marks the active bound.
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
    grey = (70, 70, 70)
    lo_s = _naive(bound_lo).strftime("%Y-%m-%d") if bound_lo else None
    hi_s = _naive(bound_hi).strftime("%Y-%m-%d") if bound_hi else None
    if not recs:
        plt.title("All series hidden")
        return False
    for i, rec in enumerate(recs):
        segments = history_segments(rec)
        if not segments:
            continue
        if not units:
            units = str(rec.get("units") or "").strip()
        scale, _ = unit_scale(units)
        # Skip scaling for percent-like units (handled as identity in unit_scale).
        unit_l = units.lower()
        if any(tok in unit_l for tok in ("percent", "%", "ratio", "index")) and "dollar" not in unit_l:
            scale = 1.0
        if colors and i < len(colors):
            color = colors[i]
        elif len(recs) == 1:
            color = CHART_LINE_COLOR
        else:
            color = CHART_PALETTE[i % len(CHART_PALETTE)]
        plt.date_form(CHART_DATE_FORM)
        for seg_t, seg_v in segments:
            scaled = [v * scale for v in seg_v]
            all_values.extend(scaled)
            draw = plt.scatter if len(scaled) == 1 else plt.plot
            if lo_s or hi_s:
                # Full segment dimmed, then in-bound overlay in color.
                draw(seg_t, scaled, marker="braille", color=grey)
                in_t: list[str] = []
                in_v: list[float] = []
                for t, v in zip(seg_t, scaled):
                    if lo_s and t < lo_s:
                        continue
                    if hi_s and t > hi_s:
                        continue
                    in_t.append(t)
                    in_v.append(v)
                if in_t:
                    draw_in = plt.scatter if len(in_v) == 1 else plt.plot
                    # No plotext label — interactive legend lives above the chart.
                    draw_in(in_t, in_v, marker="braille", color=color)
            else:
                draw(seg_t, scaled, marker="braille", color=color)
        drawn += 1
        if cursor_date is not None and cursor_point is None:
            target = _naive(cursor_date).strftime("%Y-%m-%d")
            for seg_t, seg_v in segments:
                scaled = [v * scale for v in seg_v]
                for t, v in zip(seg_t, scaled):
                    if t == target:
                        cursor_point = (t, v)
                        break
                if cursor_point is not None:
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
    # Unit lives on the y-axis (chart title may already name the metric).
    _, currency = unit_scale(units)
    if currency and unit_scale(units)[0] != 1.0:
        plt.ylabel("USD")
    else:
        plt.ylabel(units)
    ticks, labels = calendar_xticks(recs)
    if ticks:
        plt.xticks(ticks, labels)
    for bound, dim in ((bound_lo, True), (bound_hi, True)):
        if bound is None:
            continue
        day = _naive(bound).strftime("%Y-%m-%d")
        try:
            plt.vline(day)
        except Exception:  # noqa: BLE001
            pass
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
        # Prefer a readable release date; fall back to release id for provenance.
        released = format_release(self.obs.get("release_timestamp"), human=True)
        release_id = str(self.obs.get("release_id") or "")
        if released != "—":
            release_bit = released
        elif release_id:
            release_bit = release_id[:22]
        else:
            release_bit = "—"
        classes = "obs-row obs-missing" if raw is None else "obs-row"
        yield Label(
            f"{period:<12}{value:>16}  {release_bit:<22}",
            classes=classes,
        )


class SeriesValuesFallback(ModalScreen[None]):
    """When an observation has no release_id — series name · value · units."""

    DEFAULT_CSS = """
    SeriesValuesFallback {
        align: center middle;
        background: rgba(0, 0, 0, 0.35);
    }
    #sv-box {
        width: 72;
        height: auto;
        max-height: 24;
        border: tall #4ade80;
        background: #111111;
        padding: 1 2;
    }
    #sv-kicker { color: #4ade80; text-style: bold; height: 1; }
    #sv-body { color: #e0e0e0; height: auto; max-height: 18; }
    #sv-hint { color: #666; height: 1; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=True),
        Binding("enter", "dismiss", "Close", show=False),
    ]

    def __init__(self, rows: list[tuple[str, str, str]], *, title: str) -> None:
        super().__init__()
        self._rows = rows
        self._title = title

    def compose(self) -> ComposeResult:
        lines = [f"{'Series':<36}{'Value':>14}  {'Units':<12}"]
        lines.append("─" * 64)
        for name, value, units in self._rows:
            lines.append(f"{name[:36]:<36}{value:>14}  {(units or '—')[:12]:<12}")
        with Vertical(id="sv-box"):
            yield Label("OBSERVATION", id="sv-kicker")
            yield Static(
                f"[bold]{escape(self._title)}[/]\n\n" + "\n".join(lines),
                id="sv-body",
                markup=True,
            )
            yield Label("Esc to close", id="sv-hint")


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
    """Legacy list-row legend (table / fallback)."""

    def __init__(
        self,
        record: dict[str, Any],
        *,
        hidden: bool = False,
        color: Optional[tuple[int, int, int]] = None,
    ) -> None:
        super().__init__()
        self.record = record
        self.hidden = hidden
        self.color = color

    def compose(self) -> ComposeResult:
        check = "[ ]" if self.hidden else "[✓]"
        name = series_line_label(self.record)
        if self.color is not None:
            r, g, b = self.color
            swatch = f"[rgb({r},{g},{b})]●[/]"
            yield Label(
                f"{check} {swatch} {escape(name)}",
                classes="series-nav-row",
                markup=True,
            )
        else:
            yield Label(f"{check} {name}", classes="series-nav-row")


class LegendChip(Static, can_focus=True):
    """Compact horizontal legend chip — checkmark = visibility, focus = outline."""

    DEFAULT_CSS = """
    LegendChip {
        width: auto;
        height: 1;
        padding: 0 1;
        margin-right: 1;
        color: #d4d4d4;
        background: #141414;
        /* No border — Textual height-1 borders eat the content row. */
    }
    LegendChip.-hidden {
        color: #737373;
    }
    LegendChip:hover {
        background: #1f1f28;
        color: #fafafa;
    }
    LegendChip:focus {
        background: #1a1a22;
        text-style: bold underline;
        color: #fafafa;
    }
    LegendChip.-hidden:hover,
    LegendChip.-hidden:focus {
        color: #a3a3a3;
    }
    """

    class Toggled(Message):
        def __init__(self, series_id: str) -> None:
            super().__init__()
            self.series_id = series_id

    def __init__(
        self,
        record: dict[str, Any],
        *,
        hidden: bool = False,
        color: Optional[tuple[int, int, int]] = None,
    ) -> None:
        super().__init__()
        self.record = record
        self.hidden = hidden
        self.color = color
        self._paint()

    def _paint(self) -> None:
        check = "[ ]" if self.hidden else "[✓]"
        name = series_line_label(self.record)
        if self.color is not None:
            r, g, b = self.color
            swatch = f"[rgb({r},{g},{b})]●[/]"
            self.update(f"{check} {swatch} {escape(name)}")
        else:
            self.update(f"{check} {escape(name)}")
        self.set_class(self.hidden, "-hidden")

    def set_hidden(self, hidden: bool) -> None:
        self.hidden = hidden
        self._paint()

    def on_click(self) -> None:
        sid = str(self.record.get("id") or "")
        if sid:
            self.post_message(self.Toggled(sid))

    def on_key(self, event) -> None:  # noqa: ANN001
        # Space toggles here; Enter is handled by QueryScreen.action_inspect.
        if event.key == "space":
            sid = str(self.record.get("id") or "")
            if sid:
                self.post_message(self.Toggled(sid))
            event.stop()


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
        bound_lo: Optional[datetime] = None,
        bound_hi: Optional[datetime] = None,
        colors: Optional[Sequence[tuple[int, int, int]]] = None,
    ) -> None:
        plot_history(
            self.plt,
            records,
            cursor_date=cursor_date,
            bound_lo=bound_lo,
            bound_hi=bound_hi,
            colors=colors,
        )
        self.refresh()

    def plot_record(self, record: Optional[dict[str, Any]]) -> None:
        self.plot_records(None if record is None else [record])


class QueryScreen(BaseScreen):
    """Observation workbench for a saved (or workspace) query."""

    BINDINGS = [
        Binding("r", "load", "Refresh", key_display="r"),
        Binding("R", "annotate", "Add remark", show=False),
        Binding("A", "pick_remark", "Pick remark", show=False, priority=True),
        Binding("t", "mode_table", "Table", show=False),
        Binding("c", "mode_chart", "Chart", show=False),
        Binding("E", "add_entity", "Add entity", show=False),
        Binding("M", "add_metric", "Add metric", show=False),
        Binding("S", "add_series", "Add series", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("h", "obs_left", "Adjust date older", show=False),
        Binding("l", "obs_right", "Adjust date newer", show=False),
        Binding("left", "obs_left", "Adjust date older", show=False),
        Binding("right", "obs_right", "Adjust date newer", show=False),
        Binding("x", "toggle_series", "Show or hide series", show=False),
        Binding("o", "toggle_sort", "Sort observations", show=False),
        Binding("y", "copy_python", "Copy Python", show=False),
        Binding("[", "prev_group", "Previous chart group", show=False),
        Binding("]", "next_group", "Next chart group", show=False),
        Binding("tab", "toggle_bound_focus", "Toggle start/end date", show=False, priority=True),
        Binding("enter", "inspect", "Show/hide series / inspect", priority=True),
        Binding("escape", "cancel_remark", "Cancel remark / pick", show=False),
    ]

    FOOTER_CHROME: tuple[str, ...] = BaseScreen.FOOTER_CHROME

    DEFAULT_CSS = """
    #query-body {
        height: 1fr;
        padding: 0 2 0 2;
        background: #0a0a0a;
    }
    #query-status {
        color: #a3a3a3;
        height: 1;
        display: none;
        background: #0a0a0a;
    }
    #query-view-tabs {
        height: 1;
        margin: 0 0 0 0;
        padding: 0;
        background: #0a0a0a;
    }
    #query-view-tabs ActionButton.-tab {
        color: #a3a3a3;
        background: #1a1a1a;
    }
    #query-view-tabs ActionButton.-tab.-active {
        color: #0a0a0a;
        background: #86efac;
        text-style: bold;
    }
    #query-main {
        height: 1fr;
        min-height: 12;
        background: #0a0a0a;
    }
    #query-series-meta {
        height: 1;
        color: #737373;
        display: none;
        margin: 0 0 0 0;
    }
    #query-legend {
        width: 100%;
        height: auto;
        max-height: 3;
        background: #0a0a0a;
        display: none;
        margin: 0 0 0 0;
        padding: 0 0 0 0;
    }
    #query-series {
        width: 100%;
        height: auto;
        max-height: 5;
        background: #0a0a0a;
        display: none;
        border-bottom: solid #1f1f1f;
        margin-bottom: 0;
    }
    #strip-dates-label {
        color: #737373;
        width: auto;
        margin-right: 1;
    }
    #strip-dates-sep {
        color: #737373;
        width: auto;
        margin: 0 1;
    }
    #query-pane {
        width: 1fr;
        height: 1fr;
        background: #0a0a0a;
    }
    /* Metric chart title — hidden when it duplicates the query name. */
    #query-chart-title {
        height: 1;
        width: 100%;
        color: #d4d4d4;
        text-style: bold;
        margin: 0 0 0 0;
    }
    #query-chart-title.-empty { display: none; }
    #query-obs-header { color: #888; height: 1; }
    #query-obs { height: 1fr; background: #0a0a0a; }
    #query-chart {
        height: 1fr;
        width: 1fr;
        min-height: 14;
        display: none;
        background: #0a0a0a;
        border: solid #1a1a1a;
    }
    #query-obs-strip {
        height: auto;
        min-height: 3;
        max-height: 5;
        margin-top: 0;
        display: none;
    }
    #strip-line1 { color: #fafafa; text-style: bold; height: 1; }
    #strip-line2 { color: #e5e5e5; height: 1; }
    #strip-line3 { color: #737373; height: 1; }
    #strip-controls {
        height: 1;
        align: left middle;
    }
    #query-notes {
        height: auto;
        min-height: 6;
        max-height: 28;
        margin-top: 1;
        border-top: solid #333;
        padding-top: 1;
        overflow-y: auto;
    }
    #query-notes-header { height: 1; margin-bottom: 0; align: left middle; }
    #query-notes-kicker {
        width: 1fr;
        color: #f5f5f5;
        text-style: bold;
        height: 1;
    }
    #query-annotate {
        width: auto;
        height: 1;
        color: #86efac;
        background: #0a0a0a;
    }
    #query-annotate:hover {
        color: #bbf7d0;
        text-style: bold;
    }
    #query-remarks-scroll {
        height: auto;
        min-height: 4;
        max-height: 14;
        overflow-y: auto;
        background: #0a0a0a;
    }
    #query-remarks-list {
        color: #e8e8e8;
        height: auto;
        width: 100%;
    }
    #query-remark-draft {
        height: auto;
        display: none;
        margin-top: 1;
        background: #111;
        padding: 1;
        border: solid #333;
    }
    #query-remark-draft.-open { display: block; }
    #remark-name-row {
        height: 3;
        margin-bottom: 0;
        align: left middle;
    }
    #remark-name {
        width: 1fr;
        height: 3;
        background: #0a0a0a;
        border: solid #333;
        margin-right: 1;
    }
    #remark-cite {
        width: auto;
        color: #a3a3a3;
        height: 3;
        content-align: left middle;
        padding: 0 1;
    }
    #remark-text {
        background: #0a0a0a;
        border: solid #333;
        width: 100%;
        height: 3;
        margin-bottom: 0;
    }
    #remark-actions { height: 1; margin-top: 1; }
    #remark-submit-hint {
        color: #e5e5e5;
        height: 1;
        margin-top: 1;
        text-style: bold;
    }
    #strip-controls ActionButton.-tab {
        color: #e5e5e5;
        background: #262626;
        text-style: none;
    }
    #strip-controls ActionButton.-tab.-active {
        color: #0a0a0a;
        background: #60a5fa;
        text-style: bold;
    }
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
        background: #1a1a22;
        border-left: solid #60a5fa;
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
        height: 1;
        min-height: 1;
        max-height: 1;
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
        self._cursor_i = -1  # legacy single-cursor index; bounds prefer lo/hi
        self._bound_lo_i: int = 0
        self._bound_hi_i: int = 0
        self._bound_focus: str = "hi"  # "lo" | "hi" — Tab toggles; h/l nudges
        self._newest_first = True
        self._busy = False
        self._has_run_once = False
        self._needs_refresh = False
        self._run_token = 0
        self._remark_drafting = False
        self._pending_remark_delete = False
        self._remark_pick_mode = False
        self._selected_remark_i: Optional[int] = None  # 1-based when set
        self._pre_pick_hidden: set[str] = set()
        self._pre_pick_lo_i: int = 0
        self._pre_pick_hi_i: int = 0
        self._pre_pick_focus: str = "hi"
        self._pre_pick_records: list[dict[str, Any]] = []
        self._pre_pick_entity: list[str] = []
        self._pre_pick_metric: list[str] = []
        self._pre_pick_series: list[str] = []
        # Series records seen this session — survive composition drops for remark cite.
        self._obs_cache: dict[str, dict[str, Any]] = {}
        # Frequency selector options derived from metric variants / series.
        self._freq_options: list[Optional[str]] = [None]
        self._freq_source_key: tuple[str, ...] = ()
        self._legend_expanded = False
        self._remarks_collapsed = False

    def compose_body(self) -> ComposeResult:
        with Vertical(id="query-body"):
            yield QueryComposeHeader(id="query-compose")
            yield Static("", id="query-status", markup=True)
            with Horizontal(id="query-view-tabs"):
                yield ActionButton(
                    "Chart",
                    action_id="mode_chart",
                    shortcut="c",
                    variant="tab",
                    id="query-tab-chart",
                )
                yield ActionButton(
                    "Table",
                    action_id="mode_table",
                    shortcut="t",
                    variant="tab",
                    id="query-tab-table",
                )
            with Vertical(id="query-main"):
                with Vertical(id="query-pane"):
                    yield Label("", id="query-chart-title")
                    yield Label("", id="query-series-meta")
                    yield Horizontal(id="query-legend")
                    yield ListView(id="query-series")
                    yield Label(
                        f"{'Period':<12}{'Value':>16}  {'Release date':<22}",
                        id="query-obs-header",
                    )
                    yield ListView(id="query-obs")
                    yield HistoryPlot(id="query-chart")
                    with Vertical(id="query-obs-strip"):
                        with Horizontal(id="strip-controls"):
                            yield Label("Selected dates", id="strip-dates-label")
                            yield ActionButton(
                                "—",
                                action_id="bound_lo",
                                shortcut="Tab",
                                variant="secondary",
                                id="bound-lo-btn",
                            )
                            yield Label("—", id="strip-dates-sep")
                            yield ActionButton(
                                "—",
                                action_id="bound_hi",
                                shortcut="Tab",
                                variant="secondary",
                                id="bound-hi-btn",
                            )
                            yield ActionButton(
                                "Reset",
                                action_id="reset_bounds",
                                variant="secondary",
                                id="bound-reset-btn",
                            )
                        yield Label("", id="strip-line1", markup=True)
                        yield Label("", id="strip-line2", markup=True)
                        yield Label("", id="strip-line3")
            with Vertical(id="query-notes"):
                with Horizontal(id="query-notes-header"):
                    yield Label("Research remarks", id="query-notes-kicker")
                    yield ActionButton(
                        "▾",
                        action_id="toggle_remarks",
                        variant="ghost",
                        id="query-remarks-toggle",
                    )
                    yield ActionButton(
                        "+ Add remark",
                        action_id="annotate",
                        shortcut="R",
                        variant="secondary",
                        id="query-annotate",
                    )
                with VerticalScroll(id="query-remarks-scroll"):
                    yield Static("", id="query-remarks-list", markup=True)
                with Vertical(id="query-remark-draft"):
                    with Horizontal(id="remark-name-row"):
                        yield Input(placeholder="Name (optional)", id="remark-name")
                        yield Checkbox("Source", id="remark-cite", value=False)
                    yield Label("Remark", classes="remark-field-label")
                    yield TextArea("", id="remark-text")
                    yield Label(
                        "Enter to add remark",
                        id="remark-submit-hint",
                        markup=False,
                    )
                    with Horizontal(id="remark-actions"):
                        yield ActionButton(
                            "Save",
                            action_id="save_remark",
                            variant="primary",
                            id="remark-save",
                        )
                        yield ActionButton(
                            "Cancel",
                            action_id="cancel_remark",
                            variant="ghost",
                            id="remark-cancel",
                        )

    def on_mount(self) -> None:
        super().on_mount()
        self._render_idle()
        self._apply_mode()
        self._paint_compose()
        self.query_one("#query-obs", ListView).focus()
        # First-time auto-run only when the query is already executable.
        # Prefer disk cache; explicit ``r`` / refresh banner uses network.
        try:
            query = self._resolve_query()
            if self._is_executable(query) and not self._has_run_once:
                self.call_later(lambda: self.load_query(query, refresh=False))
        except Exception:  # noqa: BLE001
            pass

    def footer_status(self) -> list[str]:
        return []

    def bug_report_snapshot(self) -> dict[str, Any]:
        try:
            query = self._resolve_query()
            query_payload = query.to_dict()
            api = {"client.query": query.to_query_kwargs()}
        except Exception as exc:  # noqa: BLE001
            return {
                "kind": "query",
                "error": str(exc),
                "query_id": self.query_id,
            }
        series_ids = [
            str(r.get("id") or "")
            for r in self._records
            if r.get("id")
        ]
        return {
            "kind": "query",
            "query_id": self.query_id,
            "query": query_payload,
            "api": api,
            "loaded": self._loaded,
            "result_series_ids": series_ids,
            "result_series_count": len(self._records),
        }

    def bug_report_summary(self) -> str:
        try:
            query = self._resolve_query()
            name = query.name or f"Q{query.id}"
        except Exception:
            name = f"query {self.query_id}"
        return f"[dim]Query[/] [bold]{escape(str(name))}[/] [dim]→ client.query(…)[/]"

    def footer_action_keys(self) -> list[str]:
        # Contextual hints only — full help lives beside controls / under ?.
        if self._mode == "chart" and self._loaded:
            return [
                "[bold]←→[/bold] Adjust date",
                "[bold]Enter[/bold] Show/hide series",
            ]
        return []

    def shell_breadcrumb(self) -> str:
        try:
            query = self._resolve_query()
        except Exception:
            return "Query"
        tag = f"Q{query.id}" if query.id else "Query"
        name = query.name or tag
        mode = "Table" if self._mode == "table" else "Chart"
        return f"{tag} · {name} · {mode}"

    def refresh_footer(self) -> None:
        parts = [
            *self.footer_action_keys(),
            *self.FOOTER_CHROME,
        ]
        try:
            self.query_one("#help-hint", Label).update(" // ".join(parts))
        except Exception:  # noqa: BLE001
            pass
        self.refresh_bug_report_chrome()

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
        except Exception:
            return
        self._render_annotations(query)
        self.query_one("#query-obs-strip").display = False

    def _humanize_series_id(self, sid: str) -> str:
        """Best-effort entity · metric from a series id or obs cache."""
        labels = getattr(self.app, "labels", {}) or {}
        rec = self._obs_cache.get(sid)
        if rec is not None:
            entity = series_line_label(rec)
            metric = str(rec.get("label") or "")
            if " for " in metric:
                metric = metric.split(" for ", 1)[0].strip()
            elif rec.get("metric_id"):
                mid = str(rec["metric_id"])
                metric = label_for(labels, mid) or mid.replace("-", " ").title()
            return " · ".join(p for p in (entity, metric) if p) or sid
        parts = [p for p in str(sid).split(":") if p]
        if len(parts) >= 3:
            # edgar:CIK:metric-slug:units:freq
            cik = parts[1]
            metric_slug = parts[2]
            entity = (
                label_for(labels, f"cik:{cik}")
                or label_for(labels, cik)
                or ""
            )
            # Prefer a cached entity that matches this CIK fragment.
            if not entity or entity == cik or entity.startswith("000"):
                for cached in self._obs_cache.values():
                    if sid.rsplit(":", 2)[0] in str(cached.get("id") or ""):
                        entity = series_line_label(cached)
                        break
                    for ent in cached.get("entities") or []:
                        eid = str(ent.get("id") or "")
                        if cik in eid:
                            entity = str(ent.get("label") or eid)
                            break
            metric = label_for(labels, metric_slug) or metric_slug.replace(
                "-", " "
            ).title()
            if entity and not entity.startswith("000"):
                return f"{entity} · {metric}"
            return metric
        return label_for(labels, sid) or sid

    def _format_remark_citation(self, remark: QueryRemark) -> tuple[str, str]:
        """Return ``(source_line, span_line)`` for display."""
        names = [
            self._humanize_series_id(sid) for sid in (remark.series or [])[:3]
        ]
        extra = "…" if len(remark.series or []) > 3 else ""
        who = ", ".join(names) + extra if names else ""
        span = ""
        if remark.from_ or remark.to:
            def _year(raw: str) -> str:
                text = (raw or "").strip()
                return text[:4] if len(text) >= 4 and text[:4].isdigit() else text

            span = f"{_year(remark.from_ or '…')}–{_year(remark.to or '…')}"
        source = f"Source: {who}" if who else ""
        return source, span

    def _render_annotations(self, query: Query) -> None:
        panel = self.query_one("#query-remarks-list", Static)
        scroll = self.query_one("#query-remarks-scroll")
        kicker = self.query_one("#query-notes-kicker", Label)
        toggle = self.query_one("#query-remarks-toggle", ActionButton)
        remarks = list(query.remarks or [])
        n = len(remarks)
        kicker.update(f"Research remarks ({n})" if n else "Research remarks")
        toggle.set_label("▸" if self._remarks_collapsed else "▾")
        if self._remarks_collapsed:
            scroll.display = False
            panel.update("")
            return
        scroll.display = True
        lines: list[str] = []
        picking = self._remark_pick_mode
        selected = self._selected_remark_i
        for i, remark in enumerate(remarks, start=1):
            selected_here = picking and selected == i
            num = f"[bold #fbbf24]{i}[/] " if picking else ""
            body = escape(remark.text)
            if remark.name:
                title = escape(remark.name)
                if selected_here:
                    lines.append(f"{num}[bold #0a0a0a on #86efac] {title} [/]")
                    lines.append(f"[#bbf7d0]{body}[/]")
                else:
                    lines.append(f"{num}[bold]{title}[/]")
                    lines.append(body)
            else:
                if selected_here:
                    lines.append(f"{num}[#bbf7d0]{body}[/]")
                else:
                    lines.append(f"{num}{body}")
            source, span = self._format_remark_citation(remark)
            if source:
                lines.append(f"[dim]{escape(source)}[/]")
            if span or remark.series:
                evidence = f"[#86efac]View evidence[/] [dim](Shift+A {i})[/]"
                if span:
                    lines.append(f"[dim]{escape(span)}[/]  {evidence}")
                else:
                    lines.append(evidence)
            if i < n:
                lines.append("[dim]────────────────────────────────────────[/]")
        if not lines:
            lines.append(
                "[dim]No remarks yet. Press R to add one — optional name, "
                "required text, optional source cite from the chart. "
                "Shift+A to pick.[/]"
            )
        elif picking and selected is None:
            lines.append("[dim]Press 1–9 to highlight a remark · Esc to exit[/]")
        panel.update("\n".join(lines))

    def refresh_annotations(self) -> None:
        """Host hook after remark edits."""
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._render_annotations(query)

    def begin_add_remark(self) -> None:
        """Open the inline remark draft (``R`` / header button)."""
        if self.query_id is None:
            self.notify(
                "Save as a query first to attach remarks",
                severity="warning",
            )
            return
        if self._remark_pick_mode:
            self._exit_remark_pick(restore=True)
        self._remark_drafting = True
        draft = self.query_one("#query-remark-draft")
        draft.add_class("-open")
        # Shrink the list so the draft (incl. Enter hint) stays on screen.
        try:
            self.query_one("#query-remarks-scroll").styles.max_height = 5
        except Exception:  # noqa: BLE001
            pass
        self.query_one("#remark-name", Input).value = ""
        self.query_one("#remark-text", TextArea).load_text("")
        try:
            self.query_one("#remark-cite", Checkbox).value = False
        except Exception:  # noqa: BLE001
            pass
        self.query_one("#remark-name", Input).focus()

    def action_annotate(self) -> None:
        self.begin_add_remark()

    def action_pick_remark(self) -> None:
        """Shift+A — number the remarks and apply a picked cite to the chart."""
        if self._remark_drafting:
            return
        try:
            query = self._resolve_query()
        except Exception:
            return
        remarks = list(query.remarks or [])
        if not remarks:
            self.notify("No remarks to pick — press R to add one", severity="warning")
            return
        if not self._remark_pick_mode:
            self._snapshot_remark_pick(query)
            self._remark_pick_mode = True
            self._selected_remark_i = None
        self._render_annotations(query)
        self.notify("Pick remark 1–9 · Esc to exit", timeout=3)

    def _snapshot_remark_pick(self, query: Query) -> None:
        self._pre_pick_hidden = set(self._hidden)
        self._pre_pick_lo_i = self._bound_lo_i
        self._pre_pick_hi_i = self._bound_hi_i
        self._pre_pick_focus = self._bound_focus
        self._pre_pick_records = list(self._records)
        self._pre_pick_entity = list(query.entity)
        self._pre_pick_metric = list(query.metric)
        self._pre_pick_series = list(query.series)

    def _restore_remark_pick_baseline(self) -> None:
        """Undo temporary cite restores without leaving pick mode."""
        try:
            query = self._resolve_query()
        except Exception:
            query = None
        if query is not None:
            query.entity = list(self._pre_pick_entity)
            query.metric = list(self._pre_pick_metric)
            query.series = list(self._pre_pick_series)
        self._records = list(self._pre_pick_records)
        self._groups = group_plot_records(self._records)
        self._hidden = set(self._pre_pick_hidden)
        self._bound_lo_i = self._pre_pick_lo_i
        self._bound_hi_i = self._pre_pick_hi_i
        self._bound_focus = self._pre_pick_focus

    def _exit_remark_pick(self, *, restore: bool = True) -> None:
        if not self._remark_pick_mode:
            return
        self._remark_pick_mode = False
        self._selected_remark_i = None
        if restore:
            self._restore_remark_pick_baseline()
            self._fill_nav()
            self._show_selected()
            refresh = getattr(self.app, "refresh_shell_chrome", None)
            if callable(refresh):
                refresh()
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._render_annotations(query)

    def _select_remark(self, index: int) -> None:
        try:
            query = self._resolve_query()
        except Exception:
            return
        remarks = list(query.remarks or [])
        if index < 1 or index > len(remarks):
            self.notify(f"No remark {index}", severity="warning")
            return
        # Each pick starts from the pre-mode composition / records.
        self._restore_remark_pick_baseline()
        self._selected_remark_i = index
        self._apply_remark_cite(remarks[index - 1])
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._render_annotations(query)

    def _entity_ids_from_record(self, rec: dict[str, Any]) -> list[str]:
        out: list[str] = []
        for ent in rec.get("entities") or []:
            if isinstance(ent, dict):
                eid = str(ent.get("id") or "").strip()
            else:
                eid = str(getattr(ent, "id", "") or "").strip()
            if eid and eid not in out:
                out.append(eid)
        return out

    def _fetch_cited_series_record(self, series_id: str) -> Optional[dict[str, Any]]:
        """Load one series record, preferring session / disk cache."""
        cached = self._obs_cache.get(series_id)
        if cached is not None:
            return cached
        client = getattr(self.app, "client", None) or self.client
        if client is None:
            return None
        try:
            query = self._resolve_query()
        except Exception:
            query = None
        # Prefer /query (disk-cached) when the open query has a window size.
        try:
            kwargs: dict[str, Any] = {"series": [series_id]}
            if query is not None:
                if query.head is not None:
                    kwargs["head"] = query.head
                elif query.tail is not None:
                    kwargs["tail"] = query.tail
                else:
                    # Full cite windows need history; use a wide tail.
                    kwargs["tail"] = 500
                if query.frequency and query.frequency != "?":
                    kwargs["frequency"] = query.frequency
            results = client.query(**kwargs)
            if results:
                rec = series_to_record(results[0])
                self._obs_cache[series_id] = rec
                return rec
        except Exception:  # noqa: BLE001
            pass
        # Fall back to series metadata + observations endpoints (also cached).
        try:
            meta = client.get_series(series_id)
            start = None
            end = None
            obs = client.get_series_observations(
                series_id, start_date=start, end_date=end
            )
            ts = TimeSeries(series=meta, observations=list(obs))
            rec = series_to_record(ts)
            self._obs_cache[series_id] = rec
            return rec
        except Exception:  # noqa: BLE001
            return None

    def _ensure_cited_series(
        self, keep: set[str]
    ) -> tuple[set[str], list[str]]:
        """Restore missing cited series into ``_records`` and query entities.

        Returns ``(present_ids, restored_entity_ids)``. Temporary — undone on
        pick exit / next pick baseline restore.
        """
        present = {
            str(rec.get("id") or "").strip()
            for rec in self._records
            if rec.get("id")
        }
        restored_entities: list[str] = []
        missing = [sid for sid in keep if sid not in present]
        if not missing:
            return present, restored_entities
        try:
            query = self._resolve_query()
        except Exception:
            query = None
        added = False
        for sid in missing:
            rec = self._fetch_cited_series_record(sid)
            if rec is None:
                continue
            self._records.append(rec)
            present.add(sid)
            added = True
            if query is None:
                continue
            for eid in self._entity_ids_from_record(rec):
                if eid not in query.entity:
                    query.entity = list(query.entity) + [eid]
                    restored_entities.append(eid)
                    remember = getattr(self.app, "remember_label", None)
                    if callable(remember):
                        for ent in rec.get("entities") or []:
                            if isinstance(ent, dict) and str(ent.get("id")) == eid:
                                lab = ent.get("label")
                                if lab:
                                    remember(eid, str(lab))
                    labels = getattr(self.app, "labels", None)
                    if isinstance(labels, dict) and eid not in labels:
                        for ent in rec.get("entities") or []:
                            if isinstance(ent, dict) and str(ent.get("id")) == eid:
                                lab = ent.get("label")
                                if lab:
                                    labels[eid] = str(lab)
            mid = str(rec.get("metric_id") or "").strip()
            if mid and mid not in query.metric:
                query.metric = list(query.metric) + [mid]
        if added:
            self._groups = group_plot_records(self._records)
            refresh = getattr(self.app, "refresh_shell_chrome", None)
            if callable(refresh):
                refresh()
        return present, restored_entities

    def _apply_remark_cite(self, remark: QueryRemark) -> None:
        """Filter chart series / date bounds from a remark's cross-section cite.

        Temporarily rehydrates cited series (and their entities) that were
        dropped from the active composition, preferring session/disk cache.
        """
        restored: list[str] = []
        if remark.series:
            keep = {str(s).strip() for s in remark.series if str(s).strip()}
            present, restored = self._ensure_cited_series(keep)
            if keep & present:
                self._hidden = {
                    sid
                    for sid in {
                        str(rec.get("id") or "").strip()
                        for rec in self._records
                        if rec.get("id")
                    }
                    if sid not in keep
                }
            else:
                self.notify(
                    "Cited series unavailable — could not restore",
                    severity="warning",
                    timeout=3,
                )
                self._hidden = set()
        else:
            self._hidden = set()
        self._fill_nav()
        self._reset_bounds()
        dates = self._all_cursor_dates()
        if dates and (remark.from_ or remark.to):
            lo_day = _parse_stamp(remark.from_)
            hi_day = _parse_stamp(remark.to)

            def _as_date(value: Optional[datetime]):
                if value is None:
                    return None
                return value.date() if hasattr(value, "date") else value

            lo_target = _as_date(lo_day)
            hi_target = _as_date(hi_day)
            if lo_target is not None:
                self._bound_lo_i = 0
                for i, day in enumerate(dates):
                    if _as_date(day) >= lo_target:
                        self._bound_lo_i = i
                        break
                else:
                    self._bound_lo_i = len(dates) - 1
            if hi_target is not None:
                self._bound_hi_i = len(dates) - 1
                for i, day in enumerate(dates):
                    if _as_date(day) <= hi_target:
                        self._bound_hi_i = i
            self._clamp_bounds()
            self._cursor_i = (
                self._bound_lo_i if self._bound_focus == "lo" else self._bound_hi_i
            )
        self._show_selected()
        bits: list[str] = []
        if remark.series:
            bits.append(f"{len(remark.series)} series")
        if restored:
            bits.append(f"restored {len(restored)} entit{'y' if len(restored) == 1 else 'ies'}")
        if remark.from_ or remark.to:
            bits.append(f"{remark.from_ or '…'}–{remark.to or '…'}")
        if bits:
            self.notify("Applied cite · " + " · ".join(bits), timeout=3)
        else:
            self.notify("Remark has no cite — chart unchanged", timeout=2)

    def action_cancel_remark(self) -> None:
        if self._remark_pick_mode:
            self._exit_remark_pick(restore=True)
            self.notify("Remark pick cancelled", timeout=1)
            return
        if self._remark_drafting:
            self._remark_drafting = False
            self.query_one("#query-remark-draft").remove_class("-open")
            try:
                self.query_one("#query-remarks-scroll").styles.max_height = 14
            except Exception:  # noqa: BLE001
                pass
            self.notify("Remark cancelled", timeout=1)
            return
        # Esc still backs out of the shell when not drafting a remark.
        self.action_shell_escape()

    def _persist_remarks(self, remarks: list[QueryRemark]) -> None:
        if self.query_id is None:
            return
        inv = getattr(self.app, "investigation", None)
        if inv is None:
            return
        from ..investigation.api import update_query

        try:
            update_query(
                inv,
                int(self.query_id),
                remarks=remarks,
                app_dir=getattr(self.app, "app_dir", None),
            )
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), severity="error")
            return
        self._render_annotations(inv.get_query(int(self.query_id)))
        refresh = getattr(self.app, "refresh_shell_chrome", None)
        if callable(refresh):
            refresh()

    def action_save_remark(self) -> None:
        if not self._remark_drafting or self.query_id is None:
            return
        name = self.query_one("#remark-name", Input).value.strip() or None
        text = self.query_one("#remark-text", TextArea).text.strip()
        if not text:
            self.notify("Remark text is required", severity="warning")
            return
        cite = False
        try:
            cite = bool(self.query_one("#remark-cite", Checkbox).value)
        except Exception:  # noqa: BLE001
            pass
        series: list[str] = []
        date_from: Optional[str] = None
        date_to: Optional[str] = None
        if cite:
            for rec in self._visible_records():
                sid = str(rec.get("id") or "").strip()
                if sid and sid not in series:
                    series.append(sid)
            lo = self._bound_lo_day()
            hi = self._bound_hi_day()
            date_from = lo.strftime("%Y-%m-%d") if lo else None
            date_to = hi.strftime("%Y-%m-%d") if hi else None
        try:
            query = self._resolve_query()
        except Exception as exc:  # noqa: BLE001
            self.notify(str(exc), severity="error")
            return
        remarks = list(query.remarks or [])
        remarks.append(
            QueryRemark(
                text=text,
                name=name,
                series=series,
                from_=date_from,
                to=date_to,
            )
        )
        self._persist_remarks(remarks)
        self._remark_drafting = False
        self.query_one("#query-remark-draft").remove_class("-open")
        try:
            self.query_one("#query-remarks-scroll").styles.max_height = 14
        except Exception:  # noqa: BLE001
            pass
        self.notify("Remark saved")

    def action_delete_remark(self, index: int) -> None:
        """Delete remark at 1-based index."""
        try:
            query = self._resolve_query()
        except Exception:
            return
        remarks = list(query.remarks or [])
        if index < 1 or index > len(remarks):
            self.notify(f"No remark {index}", severity="warning")
            return
        del remarks[index - 1]
        self._persist_remarks(remarks)
        self.notify(f"Deleted remark {index}")

    def action_rename_query(self) -> None:
        if self.query_id is None:
            self.notify(
                "Save as a query first to rename it",
                severity="warning",
            )
            return
        edit = getattr(self.app, "edit_query_name", None)
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

    def _is_executable(self, query: Query) -> bool:
        return bool(query.series) or (bool(query.metric) and bool(query.entity))

    def action_load(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        if not self._is_executable(query):
            self.notify(
                "Add a metric and entity (or a series) to run this query",
                severity="warning",
            )
            return
        self._needs_refresh = False
        # Explicit refresh / ``r`` bypasses disk cache then rewrites it.
        self.load_query(query, refresh=True)

    @work(exclusive=True, group="query-view")
    async def load_query(self, query: Query, *, refresh: bool = False) -> None:
        status = self.query_one("#query-status", Static)
        self._busy = True
        self._run_token += 1
        token = self._run_token
        status.display = True
        status.update("Running /query…")
        try:
            results = await asyncio.to_thread(
                query.execute, self.client, refresh=refresh
            )
        except Exception as exc:
            if token != self._run_token:
                return
            self._busy = False
            status.update(f"Query failed: {exc}")
            self.notify(str(exc), severity="error")
            self._paint_compose()
            return
        if token != self._run_token:
            return
        self._busy = False
        self._records = [series_to_record(ts) for ts in results]
        for rec in self._records:
            sid = str(rec.get("id") or "").strip()
            if sid:
                self._obs_cache[sid] = rec
        self._groups = group_plot_records(self._records)
        self._loaded = True
        self._has_run_once = True
        self._needs_refresh = False
        self._series_i = 0
        self._group_i = 0
        self._hidden.clear()
        self._cursor_i = -1
        self._reset_bounds()
        self._mode = "chart"
        # First successful plot auto-promotes into the report set.
        if not query.in_report:
            query.in_report = True
            if not query.name:
                from ..investigation.naming import suggest_query_name

                labels = getattr(self.app, "labels", {}) or {}
                query.name = suggest_query_name(
                    query.metric, query.entity, labels=labels
                )
            from ..investigation.api import persist_investigation

            persist_investigation(
                getattr(self.app, "investigation"),
                app_dir=getattr(self.app, "app_dir", None),
            )
            # Mint a fresh empty draft slot after auto-promote.
            ensure = getattr(self.app, "ensure_draft_query", None)
            if callable(ensure):
                try:
                    ensure(reset=True)
                except Exception:  # noqa: BLE001
                    pass
            refresh = getattr(self.app, "refresh_shell_chrome", None)
            if callable(refresh):
                refresh()
        line = coverage_status(query, self._records)
        # Coverage lives once in the compose header; status is run/error only.
        status.update("")
        status.display = False
        if not self._records:
            self.notify("No series returned", severity="warning")
        elif "missing" in line:
            self.notify(line, severity="warning")
        self._fill_nav()
        self._show_selected()
        self._paint_compose(coverage=line)
        self.refresh_footer()

    def _fill_nav(self) -> None:
        nav = self.query_one("#query-series", ListView)
        legend = self.query_one("#query-legend", Horizontal)
        meta = self.query_one("#query-series-meta", Label)
        nav.clear()
        # Clear legend chips synchronously via remove_children worker-safe call.
        for child in list(legend.children):
            child.remove()
        group = self._selected_group()
        if self._mode == "chart" and group is not None:
            show = len(group.records) > 1
            nav.display = False
            visible_n = sum(
                1
                for r in group.records
                if str(r.get("id") or "") not in self._hidden
            )
            total = len(group.records)
            collapse = total > 6 and not self._legend_expanded
            meta.display = show and (collapse or total > 1)
            if show:
                meta.update(f"Series: {visible_n} of {total} visible")
            else:
                meta.update("")
            legend.display = show and not collapse
            if not show:
                return
            if collapse:
                return
            chips: list[LegendChip] = []
            for i, record in enumerate(group.records):
                hidden = str(record.get("id") or "") in self._hidden
                color = CHART_PALETTE[i % len(CHART_PALETTE)]
                chips.append(LegendChip(record, hidden=hidden, color=color))
            if chips:
                legend.mount(*chips)
                current = self._selected_record()
                cid = str(current.get("id") or "") if current else ""
                for chip in chips:
                    if str(chip.record.get("id") or "") == cid:
                        chip.focus()
                        break
            return
        legend.display = False
        meta.display = False
        meta.update("")
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

    def _all_cursor_dates(self) -> list[datetime]:
        return cursor_dates(self._visible_records())

    def _clamp_bounds(self) -> None:
        dates = self._all_cursor_dates()
        if not dates:
            # Keep prior indices so re-showing a series restores the window.
            return
        n = len(dates)
        self._bound_lo_i = max(0, min(self._bound_lo_i, n - 1))
        self._bound_hi_i = max(0, min(self._bound_hi_i, n - 1))
        if self._bound_lo_i > self._bound_hi_i:
            self._bound_lo_i, self._bound_hi_i = self._bound_hi_i, self._bound_lo_i

    def _reset_bounds(self) -> None:
        dates = self._all_cursor_dates()
        self._bound_lo_i = 0
        self._bound_hi_i = max(0, len(dates) - 1)
        self._bound_focus = "hi"
        self._cursor_i = self._bound_hi_i

    def _bound_lo_day(self) -> Optional[datetime]:
        dates = self._all_cursor_dates()
        if not dates:
            return None
        self._clamp_bounds()
        return dates[self._bound_lo_i]

    def _bound_hi_day(self) -> Optional[datetime]:
        dates = self._all_cursor_dates()
        if not dates:
            return None
        self._clamp_bounds()
        return dates[self._bound_hi_i]

    def _cursor_day(self) -> Optional[datetime]:
        """Active bound date (the one Tab-focus moves with h/l)."""
        if self._bound_focus == "lo":
            return self._bound_lo_day()
        return self._bound_hi_day()

    def _show_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            self.query_one("#query-obs-strip").display = False
            self.query_one("#query-obs", ListView).clear()
            self.query_one("#query-chart", HistoryPlot).plot_records(None)
            self._paint_chart_title()
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
        self._render_chart()
        self._render_obs_strip()
        self._apply_mode()

    def _chart_title_text(self) -> str:
        """Metric name for the chart headline (current plot group)."""
        group = self._selected_group()
        if group is not None:
            return plot_group_title(group)
        try:
            query = self._resolve_query()
        except Exception:
            return ""
        labels = getattr(self.app, "labels", {}) or {}
        if query.metric:
            mid = query.metric[0]
            return label_for(labels, mid) or mid
        if self._records:
            rec = self._records[0]
            label = str(rec.get("label") or "")
            if " for " in label:
                return label.split(" for ", 1)[0].strip()
            if rec.get("metric_id"):
                return str(rec["metric_id"]).replace("-", " ").title()
            return label
        return ""

    def _paint_chart_title(self) -> None:
        title = self._chart_title_text()
        label = self.query_one("#query-chart-title", Label)
        # Hide when the chart title just repeats the query name.
        try:
            query = self._resolve_query()
            qname = (query.name or "").strip()
        except Exception:
            qname = ""
        redundant = bool(
            title and qname and title.casefold() == qname.casefold()
        )
        units = ""
        group = self._selected_group()
        if group is not None and group.units:
            units = group.units
        elif self._visible_records():
            units = str(self._visible_records()[0].get("units") or "")
        _, currency = unit_scale(units)
        unit_bit = "USD" if currency and unit_scale(units)[0] != 1.0 else units
        if title and unit_bit and not redundant:
            display = f"{title}  ·  {unit_bit}"
        else:
            display = "" if redundant else title
        label.update(display)
        label.set_class(not bool(display), "-empty")

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
        self._paint_chart_title()
        self._clamp_bounds()
        self.query_one("#query-chart", HistoryPlot).plot_records(
            visible,
            cursor_date=self._cursor_day(),
            bound_lo=self._bound_lo_day(),
            bound_hi=self._bound_hi_day(),
            colors=colors,
        )

    def _paint_bound_buttons(self) -> None:
        try:
            lo = self.query_one("#bound-lo-btn", ActionButton)
            hi = self.query_one("#bound-hi-btn", ActionButton)
        except Exception:  # noqa: BLE001
            return
        record = self._selected_record()
        freq = (record or {}).get("frequency") if record else None
        lo_day = self._bound_lo_day()
        hi_day = self._bound_hi_day()
        lo.set_label(format_period(lo_day, freq) if lo_day else "—")
        hi.set_label(format_period(hi_day, freq) if hi_day else "—")
        lo.set_class(self._bound_focus == "lo", "-active")
        hi.set_class(self._bound_focus == "hi", "-active")

    def _render_obs_strip(self) -> None:
        strip = self.query_one("#query-obs-strip")
        if self._mode != "chart" or not self._records:
            strip.display = False
            return
        if not self._visible_records():
            strip.display = True
            self._paint_bound_buttons()
            self.query_one("#strip-line1", Label).update(
                "All series hidden"
            )
            self.query_one("#strip-line2", Label).update(
                "Enter on a series chip to show it again"
            )
            self.query_one("#strip-line3", Label).update("")
            return
        strip.display = True
        self._paint_bound_buttons()
        day = self._cursor_day()
        record = self._selected_record()
        freq = (record or {}).get("frequency") if record else None
        units = (record or {}).get("units") if record else None
        if day is None:
            self.query_one("#strip-line1", Label).update("No observations")
            self.query_one("#strip-line2", Label).update("")
            self.query_one("#strip-line3", Label).update("")
            return
        primary = (
            observation_on_date(record, day) if record is not None else None
        )
        exact = format_exact(
            primary.get("value") if primary else None, units=units
        )
        entity = series_line_label(record) if record else "—"
        period = format_period(day, freq)
        # Elevated observation inspector: identity on line 1, fact on line 2.
        self.query_one("#strip-line1", Label).update(
            f"{escape(entity)} · {escape(period)}"
        )
        reported = ""
        if primary:
            reported = format_release(
                primary.get("release_timestamp"), human=True
            )
        if reported and reported != "—":
            self.query_one("#strip-line2", Label).update(
                f"[bold]{escape(exact)}[/]     [dim]Reported {escape(reported)}[/]"
            )
        else:
            self.query_one("#strip-line2", Label).update(
                f"[bold]{escape(exact)}[/]"
            )
        focus = "start date" if self._bound_focus == "lo" else "end date"
        self.query_one("#strip-line3", Label).update(
            f"Tab focuses {focus} · ←→ adjust date"
        )

    def _paint_view_tabs(self) -> None:
        for key in ("chart", "table"):
            btn = self.query_one(f"#query-tab-{key}", ActionButton)
            btn.set_class(key == self._mode, "-active")

    def _apply_mode(self) -> None:
        if self._mode not in MODES:
            self._mode = "chart"
        table = self._mode == "table"
        chart = self._mode == "chart"
        self.query_one("#query-obs-header").display = table
        self.query_one("#query-obs").display = table
        self.query_one("#query-chart").display = chart
        self.query_one("#query-obs-strip").display = (
            chart and self._loaded and bool(self._visible_records())
        )
        order = "latest first" if self._newest_first else "oldest first"
        self.query_one("#query-obs-header", Label).update(
            f"{'Period':<12}{'Value':>16}  {'Release date':<22}  {order}"
        )
        self._paint_view_tabs()
        self._paint_chart_title()
        if table:
            self.query_one("#query-obs", ListView).focus()
        elif chart:
            chips = self._legend_chips()
            if chips:
                chips[min(self._series_i, len(chips) - 1) % len(chips)].focus()
            elif self.query_one("#query-series").display:
                self.query_one("#query-series", ListView).focus()
        self._paint_compose()
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
        # Sources view retired — keep alias for older callers.
        self.action_mode_table()

    def action_mode_raw(self) -> None:
        self.action_mode_table()

    def action_mode_definition(self) -> None:
        self.action_mode_table()

    def _obs_mode_for(self, query: Query) -> str:
        if query.tail is not None:
            return "most_recent"
        if query.head is not None:
            return "earliest"
        return "all"

    def _freq_source_key_for(self, query: Query) -> tuple[str, ...]:
        return (
            *(f"m:{m}" for m in query.metric),
            *(f"s:{s}" for s in query.series),
        )

    def _sync_frequency_options(self, query: Query) -> None:
        """Refresh frequency tokens from metric variants / direct series."""
        key = self._freq_source_key_for(query)
        if key == self._freq_source_key:
            return
        self._freq_source_key = key
        if not key:
            self._freq_options = [None]
            return
        self.refresh_frequency_options(list(query.metric), list(query.series))

    @work(exclusive=True, group="query-freq-options")
    async def refresh_frequency_options(
        self, metric_ids: list[str], series_ids: list[str]
    ) -> None:
        def _collect() -> set[str]:
            found: set[str] = set()
            for mid in metric_ids:
                try:
                    metric = self.client.get_metric(mid)
                except Exception:  # noqa: BLE001
                    continue
                for variant in metric.variants or []:
                    found.add(normalize_variant_frequency(variant.frequency))
            for sid in series_ids:
                try:
                    series = self.client.get_series(sid)
                except Exception:  # noqa: BLE001
                    continue
                found.add(normalize_variant_frequency(series.frequency))
            return found

        available = await asyncio.to_thread(_collect)
        # Drop result if composition moved on while we were fetching.
        try:
            query = self._resolve_query()
        except Exception:  # noqa: BLE001
            return
        if self._freq_source_key_for(query) != self._freq_source_key:
            return
        self._freq_options = frequency_options_for(available)
        # Re-paint so the EnumSelect picks up the new option list.
        self._paint_compose()

    def _sync_query_name(self, query: Query) -> None:
        """Refresh auto-generated titles so they match live composition."""
        from ..investigation.naming import sync_auto_query_name

        labels = getattr(self.app, "labels", {}) or {}
        updated = sync_auto_query_name(
            query.name, query.metric, query.entity, labels=labels
        )
        if updated != (query.name or ""):
            query.name = updated

    def _paint_compose(self, *, coverage: str = "") -> None:
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._sync_frequency_options(query)
        labels = getattr(self.app, "labels", {}) or {}
        from ..investigation.naming import composition_subtitle

        badge = "✓ Saved" if query.in_report else "Draft"
        if self._needs_refresh:
            badge = f"{badge} · pending refresh"
        if self._busy:
            badge = f"{badge} · running…"
        header = self.query_one("#query-compose", QueryComposeHeader)
        cov = coverage
        if not cov and self._records:
            cov = coverage_status(query, self._records)
        header.paint(
            title=query.name or f"Query {query.id}",
            badge=badge,
            entities=[(e, label_for(labels, e)) for e in query.entity],
            metrics=[(m, label_for(labels, m)) for m in query.metric],
            series=[(s, label_for(labels, s)) for s in query.series],
            frequency=query.frequency,
            obs_mode=self._obs_mode_for(query),
            mode=self._mode,
            needs_refresh=self._needs_refresh,
            coverage=cov,
            subtitle=composition_subtitle(
                query.metric, query.entity, query.series
            ),
            available_frequencies=self._freq_options,
        )

    def _mark_composition_changed(self) -> None:
        """After the first plot, further edits require an explicit refresh."""
        from ..investigation.api import persist_investigation

        try:
            query = self._resolve_query()
            self._sync_query_name(query)
        except Exception:
            pass
        persist_investigation(
            getattr(self.app, "investigation"),
            app_dir=getattr(self.app, "app_dir", None),
        )
        refresh = getattr(self.app, "refresh_shell_chrome", None)
        if callable(refresh):
            refresh()
        try:
            query = self._resolve_query()
        except Exception:
            return
        if not self._has_run_once:
            if self._is_executable(query):
                self.action_load()
            else:
                self._paint_compose()
            return
        self._needs_refresh = True
        self.notify("Composition changed — press r to refresh", timeout=4)
        self._paint_compose()

    def _append_query_resource(self, kind: str, result: dict) -> None:
        """Persist a picked resource onto the active query."""
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        rid = str(result.get("id") or "")
        if not rid:
            return
        bag = (
            query.entity
            if kind == "entity"
            else query.metric
            if kind == "metric"
            else query.series
        )
        if rid in bag:
            self.notify(f"{rid} already in query", severity="warning")
            return
        if kind == "entity":
            query.entity = list(query.entity) + [rid]
        elif kind == "metric":
            query.metric = list(query.metric) + [rid]
        else:
            query.series = list(query.series) + [rid]
        labels = getattr(self.app, "labels", {}) or {}
        if result.get("label"):
            labels[rid] = str(result["label"])
            remember = getattr(self.app, "remember_label", None)
            if callable(remember):
                remember(rid, str(result["label"]))
        self._sync_query_name(query)
        self._mark_composition_changed()

    def _remove_query_resource(self, kind: str, resource_id: str) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        if kind == "entity" and resource_id in query.entity:
            query.entity = [x for x in query.entity if x != resource_id]
        elif kind == "metric" and resource_id in query.metric:
            query.metric = [x for x in query.metric if x != resource_id]
        elif kind == "series" and resource_id in query.series:
            query.series = [x for x in query.series if x != resource_id]
        else:
            return
        self._sync_query_name(query)
        self._mark_composition_changed()

    def _open_scoped_picker(self, kind: str) -> None:
        """Pick entity/metric/series; scope when a seed resource exists."""
        if self.query_id is None:
            self.notify("Open a query first", severity="warning")
            return
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return

        from ..investigation.resource_picker import ResourcePickerModal

        labels = getattr(self.app, "labels", {}) or {}
        seed_metric = query.metric[0] if query.metric else None
        seed_entity = query.entity[0] if query.entity else None
        scope_metric = seed_metric if kind == "entity" and seed_metric else None
        scope_entity = seed_entity if kind == "metric" and seed_entity else None
        scope_label = None
        if scope_metric:
            scope_label = label_for(labels, scope_metric)
        elif scope_entity:
            scope_label = label_for(labels, scope_entity)
        exclude = list(
            query.entity
            if kind == "entity"
            else query.metric
            if kind == "metric"
            else query.series
        )

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
                scope_metric=scope_metric,
                scope_entity=scope_entity,
                scope_label=scope_label,
                exclude_ids=exclude,
            ),
            _done,
        )

    def action_add_entity(self) -> None:
        self._open_scoped_picker("entity")

    def action_add_metric(self) -> None:
        self._open_scoped_picker("metric")

    def action_add_series(self) -> None:
        self._open_scoped_picker("series")

    def action_add_compare(self) -> None:
        self.action_add_entity()

    @on(QueryComposeHeader.AddResource)
    def _compose_add(self, event: QueryComposeHeader.AddResource) -> None:
        event.stop()
        self._open_scoped_picker(event.kind)

    @on(QueryComposeHeader.RemoveResource)
    def _compose_remove(self, event: QueryComposeHeader.RemoveResource) -> None:
        event.stop()
        self._remove_query_resource(event.kind, event.resource_id)

    @on(QueryComposeHeader.ScopeChanged)
    def _compose_scope(self, event: QueryComposeHeader.ScopeChanged) -> None:
        event.stop()
        try:
            query = self._resolve_query()
        except Exception:
            return
        query.frequency = event.frequency
        if event.obs_mode == "all":
            query.head = None
            query.tail = None
        elif event.obs_mode == "earliest":
            query.head = 20
            query.tail = None
        else:
            query.head = None
            query.tail = 20
        self._mark_composition_changed()

    @on(QueryComposeHeader.Refresh)
    def _compose_refresh(self, event: QueryComposeHeader.Refresh) -> None:
        event.stop()
        self.action_load()

    @on(QueryComposeHeader.Rename)
    def _compose_rename(self, event: QueryComposeHeader.Rename) -> None:
        event.stop()
        self.action_rename_query()

    @on(ActionButton.Pressed)
    def _action_button(self, event: ActionButton.Pressed) -> None:
        event.stop()
        if event.button_id == "mode_chart":
            self.action_mode_chart()
        elif event.button_id == "mode_table":
            self.action_mode_table()
        elif event.button_id == "annotate":
            self.action_annotate()
        elif event.button_id == "toggle_remarks":
            self.action_toggle_remarks()
        elif event.button_id == "save_remark":
            self.action_save_remark()
        elif event.button_id == "cancel_remark":
            self.action_cancel_remark()
        elif event.button_id == "bound_lo":
            self._bound_focus = "lo"
            self._render_chart()
            self._render_obs_strip()
        elif event.button_id == "bound_hi":
            self._bound_focus = "hi"
            self._render_chart()
            self._render_obs_strip()
        elif event.button_id == "reset_bounds":
            self.action_reset_bounds()

    def action_obs_left(self) -> None:
        dates = self._all_cursor_dates()
        if not dates:
            return
        self._clamp_bounds()
        if self._bound_focus == "lo":
            self._bound_lo_i = max(0, self._bound_lo_i - 1)
        else:
            self._bound_hi_i = max(self._bound_lo_i, self._bound_hi_i - 1)
        self._cursor_i = (
            self._bound_lo_i if self._bound_focus == "lo" else self._bound_hi_i
        )
        self._render_chart()
        self._render_obs_strip()

    def action_obs_right(self) -> None:
        dates = self._all_cursor_dates()
        if not dates:
            return
        self._clamp_bounds()
        last = len(dates) - 1
        if self._bound_focus == "lo":
            self._bound_lo_i = min(self._bound_hi_i, self._bound_lo_i + 1)
        else:
            self._bound_hi_i = min(last, self._bound_hi_i + 1)
        self._cursor_i = (
            self._bound_lo_i if self._bound_focus == "lo" else self._bound_hi_i
        )
        self._render_chart()
        self._render_obs_strip()

    def _cycle_remark_focus(self) -> None:
        """Tab within the inline remark draft: name → text → cite → save."""
        order = [
            self.query_one("#remark-name", Input),
            self.query_one("#remark-text", TextArea),
            self.query_one("#remark-cite", Checkbox),
            self.query_one("#remark-save", ActionButton),
        ]
        focused = self.focused
        try:
            idx = next(i for i, w in enumerate(order) if w is focused)
        except StopIteration:
            order[0].focus()
            return
        order[(idx + 1) % len(order)].focus()

    def action_toggle_bound_focus(self) -> None:
        # While drafting a remark, Tab stays inside that pane.
        if self._remark_drafting:
            self._cycle_remark_focus()
            return
        self._bound_focus = "hi" if self._bound_focus == "lo" else "lo"
        self._cursor_i = (
            self._bound_lo_i if self._bound_focus == "lo" else self._bound_hi_i
        )
        self._render_chart()
        self._render_obs_strip()
        which = "start date" if self._bound_focus == "lo" else "end date"
        self.notify(f"Adjusting {which}", timeout=1)

    def action_reset_bounds(self) -> None:
        self._hidden.clear()
        self._reset_bounds()
        self._fill_nav()
        self._show_selected()
        self.notify("Reset period and series visibility", timeout=2)

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
        # Remark draft: Enter submits.
        if self._remark_drafting:
            self.action_save_remark()
            return
        # Chart: Enter toggles the focused legend chip / selected series.
        if self._mode == "chart":
            focused = self.focused
            if isinstance(focused, LegendChip):
                try:
                    self._series_i = self._records.index(focused.record)
                except ValueError:
                    pass
            self.action_toggle_series()
            return
        child = self.query_one("#query-obs", ListView).highlighted_child
        if isinstance(child, ObservationRow):
            self._inspect_table_observation(child.record, child.obs)
            return
        self.notify("No observation highlighted", severity="warning")

    def _inspect_table_observation(
        self, record: dict[str, Any], obs: dict[str, Any]
    ) -> None:
        """Table Enter → release page when possible; else series|value|units."""
        release_id = str(obs.get("release_id") or "").strip()
        if release_id:
            open_fn = getattr(self.app, "open_resource_view", None)
            if callable(open_fn):
                open_fn("release", release_id, label=release_id)
                return
        day = _parse_stamp(obs.get("observation_timestamp"))
        freq = record.get("frequency")
        title = format_period(day, freq) if day else "Observation"
        rows: list[tuple[str, str, str]] = []
        for rec in self._records or [record]:
            match = (
                observation_on_date(rec, day)
                if day is not None
                else (rec.get("last") if rec is record else None)
            )
            if match is None and rec is record:
                match = obs
            if match is None:
                continue
            name = series_line_label(rec)
            units = str(rec.get("units") or "")
            rows.append(
                (
                    name,
                    format_exact(match.get("value"), units=units),
                    units,
                )
            )
        if not rows:
            name = series_line_label(record)
            units = str(record.get("units") or "")
            rows.append(
                (name, format_exact(obs.get("value"), units=units), units)
            )
        self.app.push_screen(SeriesValuesFallback(rows, title=title))

    def _legend_chips(self) -> list[LegendChip]:
        try:
            legend = self.query_one("#query-legend", Horizontal)
        except Exception:  # noqa: BLE001
            return []
        return [c for c in legend.children if isinstance(c, LegendChip)]

    def _cycle_legend_focus(self, delta: int) -> bool:
        chips = self._legend_chips()
        if not chips:
            return False
        focused = self.focused
        try:
            idx = next(i for i, c in enumerate(chips) if c is focused)
        except StopIteration:
            idx = -1 if delta > 0 else 0
        nxt = chips[(idx + delta) % len(chips)]
        nxt.focus()
        try:
            self._series_i = self._records.index(nxt.record)
        except ValueError:
            pass
        self._show_selected()
        return True

    def action_cursor_down(self) -> None:
        # On the chart, j/k walk the horizontal legend chips.
        if self._mode == "chart" and not self._remark_drafting:
            if self._cycle_legend_focus(1):
                return
            series = self.query_one("#query-series", ListView)
            if series.display:
                series.focus()
                series.action_cursor_down()
                return
        focused = self.focused
        if isinstance(focused, ListView) and hasattr(focused, "action_cursor_down"):
            focused.action_cursor_down()
            return
        self.query_one("#query-obs", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._mode == "chart" and not self._remark_drafting:
            if self._cycle_legend_focus(-1):
                return
            series = self.query_one("#query-series", ListView)
            if series.display:
                series.focus()
                series.action_cursor_up()
                return
        focused = self.focused
        if isinstance(focused, ListView) and hasattr(focused, "action_cursor_up"):
            focused.action_cursor_up()
            return
        self.query_one("#query-obs", ListView).action_cursor_up()

    def action_toggle_remarks(self) -> None:
        self._remarks_collapsed = not self._remarks_collapsed
        try:
            query = self._resolve_query()
        except Exception:
            return
        self._render_annotations(query)

    def action_toggle_legend(self) -> None:
        self._legend_expanded = not self._legend_expanded
        self._fill_nav()

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

    def on_key(self, event) -> None:  # noqa: ANN001
        # Remark draft: Shift+Enter inserts a newline in the body.
        if self._remark_drafting and event.key == "shift+enter":
            focused = self.focused
            if isinstance(focused, TextArea):
                focused.insert("\n")
                event.stop()
                event.prevent_default()
                return
        # Shift+A pick mode: digit selects a remark.
        if self._remark_pick_mode and event.key in "123456789":
            if not isinstance(self.focused, (Input, TextArea)):
                self._select_remark(int(event.key))
                event.stop()
                event.prevent_default()
                return
        # D then digit deletes a remark (D1…D9).
        if getattr(self, "_pending_remark_delete", False):
            self._pending_remark_delete = False
            if event.key in "123456789":
                self.action_delete_remark(int(event.key))
                event.stop()
                event.prevent_default()
                return
        if event.key == "D" and not isinstance(self.focused, (Input, TextArea)):
            self._pending_remark_delete = True
            self.notify("Delete remark: press 1–9", timeout=2)
            event.stop()
            event.prevent_default()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._remark_pick_mode:
            if action in {"cancel_remark", "pick_remark"}:
                return True
            if action in {
                "add_entity",
                "add_metric",
                "add_series",
                "annotate",
                "obs_left",
                "obs_right",
                "toggle_bound_focus",
            }:
                return False
        if self._remark_drafting:
            # Tab cycles the draft; Enter submits; Esc cancels.
            if action == "toggle_bound_focus":
                return True
            if action == "inspect":
                return True
            if action == "cancel_remark":
                return True
            if action in {
                "add_entity",
                "add_metric",
                "add_series",
                "obs_left",
                "obs_right",
                "annotate",
                "pick_remark",
                "cursor_down",
                "cursor_up",
            }:
                return False
        if isinstance(self.focused, (Input, TextArea, Checkbox)):
            if action in {
                "add_entity",
                "add_metric",
                "add_series",
                "obs_left",
                "obs_right",
                "annotate",
                "pick_remark",
            }:
                return False
            if action == "cancel_remark":
                return True
        loaded = {
            "inspect",
            "mode_table",
            "mode_chart",
            "obs_left",
            "obs_right",
            "toggle_series",
            "toggle_bound_focus",
            "reset_bounds",
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

    @on(LegendChip.Toggled)
    def on_legend_chip_toggled(self, event: LegendChip.Toggled) -> None:
        event.stop()
        sid = event.series_id
        for i, rec in enumerate(self._records):
            if str(rec.get("id") or "") == sid:
                self._series_i = i
                break
        self.action_toggle_series()

    def on_click(self, event) -> None:  # noqa: ANN001
        # Collapsed legend summary expands the chip row.
        widget = getattr(event, "widget", None) or getattr(event, "control", None)
        if getattr(widget, "id", None) == "query-series-meta":
            group = self._selected_group()
            if group is not None and len(group.records) > 6:
                self.action_toggle_legend()
                event.stop()
                return
        # Fall through to BaseScreen / default.

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
            "Interactive query composition and chart workbench. "
            "First executable plot runs automatically; further edits need r. "
            "Remarks: R to add, Shift+A to pick/apply a source; Tab switches start/end date."
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
            ActionBinding("r", "load", "Refresh / run GET /query"),
            ActionBinding("R", "annotate", "Add an inline remark"),
            ActionBinding("A", "pick_remark", "Number remarks and apply a source cite"),
            ActionBinding("c / t", "mode", "Chart or table"),
            ActionBinding("E", "add_entity", "Add entity (scoped when possible)"),
            ActionBinding("M", "add_metric", "Add metric (scoped when possible)"),
            ActionBinding("S", "add_series", "Add direct series"),
            ActionBinding("Tab", "bound", "Toggle start/end date"),
            ActionBinding("h / l", "obs", "Adjust the focused date"),
            ActionBinding("enter", "inspect", "Chart: show/hide series; table: open release"),
            ActionBinding("x", "toggle_series", "Show or hide a series on the chart"),
            ActionBinding("D + digit", "delete_remark", "Delete remark 1–9"),
            ActionBinding("[ / ]", "group", "Cycle incompatible-unit chart groups"),
            ActionBinding("o", "sort", "Toggle observation table sort"),
            ActionBinding("y", "copy_python", "Copy a Python query snippet"),
            ActionBinding("j / k", "cursor", "Move series legend or observations"),
        ),
        example=':add query --args \'{"query_id":1}\'',
    )
)
