"""Barebones terminal bar charts for discover preview and rank boards."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import AbstractSet, Iterable, Optional, Sequence

from ..models import Entity, TimeSeries

FREQ_TIEBREAK = ("Annual", "Quarterly", "Monthly", "Daily", "Intraday")
_EIGHTHS = " ▁▂▃▄▅▆▇█"
BAR_HEIGHT = 3
LABEL_WIDTH = 12
VALUE_WIDTH = 7


def pick_frequency(series_list: Sequence[TimeSeries]) -> Optional[str]:
    """Most common frequency; ties prefer coarser frequencies."""
    counts = Counter(
        ts.series.frequency for ts in series_list if ts.series.frequency
    )
    if not counts:
        return None
    top = max(counts.values())
    tied = [freq for freq, n in counts.items() if n == top]
    rank = {name: i for i, name in enumerate(FREQ_TIEBREAK)}
    tied.sort(key=lambda freq: rank.get(freq, 99))
    return tied[0]


def pick_series_per_entity(
    series_list: Sequence[TimeSeries],
    entity_ids: Sequence[str],
    frequency: str,
) -> dict[str, Optional[TimeSeries]]:
    """One series per entity at ``frequency`` (most in-window obs, then id)."""
    by_entity: dict[str, list[TimeSeries]] = defaultdict(list)
    wanted = set(entity_ids)
    for ts in series_list:
        if ts.series.frequency != frequency:
            continue
        matched = [ent.id for ent in ts.series.entities if ent.id in wanted]
        if not matched and len(entity_ids) == 1 and not ts.series.entities:
            matched = [entity_ids[0]]
        for eid in matched:
            by_entity[eid].append(ts)

    picked: dict[str, Optional[TimeSeries]] = {}
    for eid in entity_ids:
        candidates = by_entity.get(eid, [])
        if not candidates:
            picked[eid] = None
            continue
        candidates.sort(key=lambda ts: (-len(ts.observations), ts.series.id))
        picked[eid] = candidates[0]
    return picked


def _as_naive(ts: datetime) -> datetime:
    if ts.tzinfo is not None:
        return ts.replace(tzinfo=None)
    return ts


def downsample(
    observations: Sequence,
    n_bins: int,
) -> list[tuple[datetime, Optional[float]]]:
    """Bin observations into at most ``n_bins`` (mean per bin; gaps are None)."""
    if n_bins <= 0 or not observations:
        return []
    obs = sorted(observations, key=lambda o: _as_naive(o.observation_timestamp))
    if len(obs) <= n_bins:
        return [(_as_naive(o.observation_timestamp), o.value) for o in obs]
    start = _as_naive(obs[0].observation_timestamp)
    end = _as_naive(obs[-1].observation_timestamp)
    span = (end - start).total_seconds() or 1.0
    buckets: list[list[float]] = [[] for _ in range(n_bins)]
    for o in obs:
        t = (_as_naive(o.observation_timestamp) - start).total_seconds() / span
        idx = min(n_bins - 1, max(0, int(t * n_bins)))
        buckets[idx].append(o.value)
    out: list[tuple[datetime, Optional[float]]] = []
    for i, vals in enumerate(buckets):
        stamp = start + timedelta(seconds=span * (i + 0.5) / n_bins)
        out.append((stamp, mean(vals) if vals else None))
    return out


def _column_stack(frac: Optional[float], height: int) -> list[str]:
    """Top-to-bottom characters for one time column."""
    if frac is None or frac <= 0:
        return [" "] * height
    total = height * 8
    filled = max(1, min(total, int(round(frac * total))))
    cells: list[str] = []
    remaining = filled
    for _ in range(height):
        if remaining >= 8:
            cells.append("█")
            remaining -= 8
        elif remaining > 0:
            cells.append(_EIGHTHS[remaining])
            remaining = 0
        else:
            cells.append(" ")
    return list(reversed(cells))


def _scale(values: Iterable[Optional[float]]) -> tuple[float, float]:
    nums = [v for v in values if v is not None]
    if not nums:
        return 0.0, 1.0
    lo, hi = min(nums), max(nums)
    if lo >= 0:
        lo = 0.0
    if hi == lo:
        hi = lo + 1.0
    return lo, hi


def _label(text: str, width: int = LABEL_WIDTH) -> str:
    text = text or ""
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def _trim(n: float, decimals: int = 2) -> str:
    text = f"{n:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_compact(value: float) -> str:
    """Short magnitude for the preview gutter (SI suffixes, no unit)."""
    if value != value:  # NaN
        return "—"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    v = abs(value)
    for thresh, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k")):
        if v >= thresh:
            return sign + _trim(v / thresh) + suffix
    if v >= 100:
        return sign + _trim(v, 0)
    if v >= 10:
        return sign + _trim(v, 1)
    if v >= 1:
        return sign + _trim(v, 2)
    return sign + _trim(v, 3)


def pick_entity(
    entities: Sequence[Entity],
    taxonomy_ids: Optional[AbstractSet[str]] = None,
) -> Optional[Entity]:
    """Prefer a taxonomy member when known; otherwise the first entity."""
    if not entities:
        return None
    if taxonomy_ids:
        for ent in entities:
            if ent.id in taxonomy_ids:
                return ent
    return entities[0]


def last_observation_value(ts: TimeSeries) -> Optional[float]:
    """Chronologically last observation value on a series (rank key)."""
    if not ts.observations:
        return None
    latest = max(
        ts.observations,
        key=lambda o: _as_naive(o.observation_timestamp),
    )
    return latest.value


def last_observation_timestamp(ts: TimeSeries) -> Optional[datetime]:
    if not ts.observations:
        return None
    latest = max(
        ts.observations,
        key=lambda o: _as_naive(o.observation_timestamp),
    )
    return _as_naive(latest.observation_timestamp)


def shared_pane_max(values: Iterable[Optional[float]]) -> float:
    """Positive ceiling for shared-scale bars (0 → pane max)."""
    nums = [v for v in values if v is not None]
    if not nums:
        return 1.0
    hi = max(nums)
    if hi <= 0:
        return 1.0
    return hi


def horizontal_bar(frac: float, width: int) -> str:
    """One-line unicode bar; ``frac`` is 0..1 against a shared max."""
    if width <= 0:
        return ""
    if frac is None or frac <= 0:
        return " " * width
    total = width * 8
    filled = max(1, min(total, int(round(min(1.0, frac) * total))))
    full, rem = divmod(filled, 8)
    cells = ["█"] * full
    if rem and len(cells) < width:
        cells.append(_EIGHTHS[rem])
    while len(cells) < width:
        cells.append(" ")
    return "".join(cells[:width])


def format_rank_line(
    rank: int,
    name: str,
    value: float,
    pane_max: float,
    bar_width: int,
    name_width: int = 18,
) -> str:
    """One leaderboard row: rank, name, compact value, shared-scale bar."""
    frac = (value / pane_max) if pane_max > 0 else 0.0
    if value < 0:
        frac = 0.0
    return (
        f"{rank:>3}  {_label(name, name_width)}  "
        f"{format_compact(value).rjust(VALUE_WIDTH)}  "
        f"{horizontal_bar(frac, bar_width)}"
    )


def _axis_line(
    stamps: Sequence[datetime],
    bar_width: int,
    gutter: int = LABEL_WIDTH + 1,
) -> str:
    if not stamps or bar_width <= 0:
        return ""
    years = [
        stamps[0].strftime("%Y"),
        stamps[len(stamps) // 2].strftime("%Y"),
        stamps[-1].strftime("%Y"),
    ]
    uniq: list[str] = []
    for year in years:
        if year not in uniq:
            uniq.append(year)
    if bar_width < 4:
        return " " * gutter + "–".join(uniq)
    labels = [
        (0, uniq[0]),
        (max(0, bar_width // 2 - 2), uniq[len(uniq) // 2]),
        (max(0, bar_width - 4), uniq[-1]),
    ]
    line = [" "] * bar_width
    used: set[int] = set()
    for pos, lab in labels:
        if len(lab) > bar_width:
            continue
        pos = min(max(0, pos), bar_width - len(lab))
        if any(pos + i in used for i in range(len(lab))):
            continue
        for i, ch in enumerate(lab):
            line[pos + i] = ch
            used.add(pos + i)
    return " " * gutter + "".join(line)


def render_preview(
    metric_name: str,
    frequency: str,
    units: str,
    entity_labels: dict[str, str],
    picked: dict[str, Optional[TimeSeries]],
    bar_width: int = 48,
    bar_height: int = BAR_HEIGHT,
) -> str:
    """Per-series unicode bars with that series' max in a right gutter.

    Each small-multiple is scaled to its own range so a country with a
    smaller magnitude still shows shape. The compact max is the y-axis:
    the tallest bar in a row is that number.
    """
    plot_width = max(8, bar_width - VALUE_WIDTH - 2)
    binned: dict[str, list[tuple[datetime, Optional[float]]]] = {}
    series_max: dict[str, float] = {}
    all_stamps: list[datetime] = []
    for eid, ts in picked.items():
        if ts is None or not ts.observations:
            binned[eid] = []
            continue
        points = downsample(ts.observations, plot_width)
        binned[eid] = points
        raw = [o.value for o in ts.observations if o.value is not None]
        if raw:
            series_max[eid] = max(raw)
        all_stamps.extend(t for t, v in points if v is not None)

    header_bits = [metric_name or "metric"]
    if frequency:
        header_bits.append(frequency)
    if units:
        header_bits.append(units)
    if all_stamps:
        header_bits.append(
            f"{min(all_stamps).year}–{max(all_stamps).year}"
        )
    lines = [" · ".join(header_bits), ""]

    for eid, ts in picked.items():
        name = _label(entity_labels.get(eid, eid))
        points = binned.get(eid) or []
        if not points:
            lines.append(f"{name} (no series)")
            lines.append("")
            continue
        vmin, vmax = _scale(v for _, v in points)
        span = vmax - vmin or 1.0
        columns = []
        for _, value in points:
            if value is None:
                frac = None
            else:
                frac = (value - vmin) / span
            columns.append(_column_stack(frac, bar_height))
        peak = series_max.get(eid)
        max_bit = (
            format_compact(peak).rjust(VALUE_WIDTH) if peak is not None else ""
        )
        mid = bar_height // 2
        for row in range(bar_height):
            prefix = name if row == mid else " " * LABEL_WIDTH
            value_col = max_bit if row == mid and max_bit else " " * VALUE_WIDTH
            bars = "".join(col[row] for col in columns)
            lines.append(f"{prefix} {value_col} {bars}")
        lines.append("")

    if all_stamps:
        longest = max(binned.values(), key=len) if binned else []
        stamps = [t for t, _ in longest] or sorted(all_stamps)
        lines.append(
            _axis_line(
                stamps,
                min(plot_width, len(stamps)),
                gutter=LABEL_WIDTH + 1 + VALUE_WIDTH + 1,
            )
        )

    return "\n".join(lines).rstrip() + "\n"
