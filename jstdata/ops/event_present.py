"""Human-readable presentation for action-log events (research trail).

Pure formatting over existing event + artifact payloads — no API calls and no
store schema changes. Used by the Shift+L Research activity drawer and the
structured artifact inspect modal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Optional, Sequence

EventKind = Literal["search", "explore", "retrieve", "mutate", "remark", "error", "other"]

_RETRIEVE_OPS = frozenset(
    {
        "query.run",
        "query.inspect",
        "coverage.preview",
    }
)


@dataclass(frozen=True)
class PreviewRow:
    """One sample line for an expanded retrieval card."""

    label: str
    period: str
    value: str


@dataclass(frozen=True)
class PresentedEvent:
    """Display model for one action-log event."""

    event_id: str
    operation: str
    title: str
    kind: EventKind
    actor_label: str
    actor_raw: str
    status: str
    when: str  # HH:MM local-ish from completed_at
    day_key: str  # YYYY-MM-DD for grouping
    secondary_lines: tuple[str, ...] = ()
    stats_line: Optional[str] = None
    preview_rows: tuple[PreviewRow, ...] = ()
    expandable: bool = False
    error: Optional[str] = None
    # Raw bits for Request details (not shown on the card by default).
    details: dict[str, Any] = field(default_factory=dict)


def actor_display(actor: Optional[str]) -> str:
    """Map stored actor → trail label (You / CLI / Agent…)."""
    raw = (actor or "").strip()
    if not raw:
        return "—"
    lower = raw.lower()
    if lower == "tui":
        return "You"
    if lower == "cli":
        return "CLI"
    if lower == "agent":
        return "Agent"
    if lower.startswith("agent:"):
        name = raw.split(":", 1)[1].strip()
        return f"Agent:{name}" if name else "Agent"
    return raw


def actor_filter_label(actor_filter: Optional[str]) -> str:
    """Label for the drawer filter chrome (`a` cycle)."""
    if not actor_filter:
        return "All"
    return {
        "tui": "You",
        "cli": "CLI",
        "agent": "Agent",
    }.get(actor_filter, actor_filter)


def is_retrieve_op(operation: Optional[str]) -> bool:
    return (operation or "") in _RETRIEVE_OPS


def format_event(
    event: dict[str, Any],
    *,
    artifact_data: Any = ...,
) -> PresentedEvent:
    """Build a :class:`PresentedEvent` from a list/get event dict.

    Pass ``artifact_data`` explicitly when the list row has no embedded
    artifact (drawer loads it on expand). Use ``...`` to read
    ``event["artifact"]["data"]`` when present.
    """
    op = str(event.get("operation") or "?")
    status = str(event.get("status") or "?")
    inputs = event.get("inputs") if isinstance(event.get("inputs"), dict) else {}
    error = event.get("error")
    if artifact_data is ...:
        art = event.get("artifact")
        if isinstance(art, dict):
            artifact_data = art.get("data")
        else:
            artifact_data = None

    when, day_key = _when_parts(event.get("completed_at") or event.get("started_at"))
    actor_raw = (event.get("actor") or "").strip()
    actor_label = actor_display(actor_raw)

    if status == "error":
        kind: EventKind = "error"
        title = _error_title(op)
        secondary, stats, preview = _error_body(op, inputs, error, artifact_data)
    else:
        kind, title, secondary, stats, preview = _success_body(
            op, inputs, artifact_data
        )

    expandable = bool(preview) or (
        kind in {"retrieve", "remark", "search", "explore"} and artifact_data is not None
    )

    return PresentedEvent(
        event_id=str(event.get("event_id") or "?"),
        operation=op,
        title=title,
        kind=kind,
        actor_label=actor_label,
        actor_raw=actor_raw or "—",
        status=status,
        when=when,
        day_key=day_key,
        secondary_lines=tuple(secondary),
        stats_line=stats,
        preview_rows=tuple(preview),
        expandable=expandable,
        error=str(error) if error else None,
        details={
            "event_id": event.get("event_id"),
            "operation": op,
            "actor": actor_raw or None,
            "status": status,
            "inputs": inputs,
            "error": error,
            "artifact_id": event.get("artifact_id"),
            "cache_hit": event.get("cache_hit"),
            "duration_ms": event.get("duration_ms"),
        },
    )


def count_trail_stats(events: Sequence[dict[str, Any]]) -> tuple[int, int]:
    """Return ``(n_actions, n_data_requests)`` for subtitle chrome."""
    n = len(events)
    m = sum(1 for e in events if is_retrieve_op(e.get("operation")))
    return n, m


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _when_parts(stamp: Optional[str]) -> tuple[str, str]:
    if not stamp:
        return "—", ""
    text = str(stamp)
    day = text[:10] if len(text) >= 10 else text
    # Prefer HH:MM from ISO timestamp.
    time = "—"
    if "T" in text:
        clock = text.split("T", 1)[1]
        time = clock[:5] if len(clock) >= 5 else clock
    elif len(text) >= 16:
        time = text[11:16]
    return time, day


def _error_title(op: str) -> str:
    if op in _RETRIEVE_OPS or op.startswith("query."):
        return "Data unavailable"
    return "Action failed"


def _error_body(
    op: str,
    inputs: dict[str, Any],
    error: Any,
    artifact_data: Any,
) -> tuple[list[str], Optional[str], list[PreviewRow]]:
    lines: list[str] = []
    subject = _subject_from_inputs(op, inputs, artifact_data)
    if subject:
        lines.append(subject)
    reason = str(error).strip() if error else "Unknown error"
    if len(reason) > 120:
        reason = reason[:117] + "…"
    lines.append(reason)
    return lines, None, []


def _success_body(
    op: str,
    inputs: dict[str, Any],
    artifact_data: Any,
) -> tuple[EventKind, str, list[str], Optional[str], list[PreviewRow]]:
    if op == "search.entities":
        return _search_body("Searched entities", inputs, artifact_data)
    if op == "search.metrics":
        return _search_body("Searched metrics", inputs, artifact_data)
    if op == "metadata.entity":
        return (
            "explore",
            "Explored entity",
            _lines_nonempty([_label_or_id(inputs.get("entity_id") or inputs.get("id"), artifact_data)]),
            None,
            [],
        )
    if op == "metadata.metric":
        return (
            "explore",
            "Explored metrics",
            _lines_nonempty(
                [
                    _label_or_id(
                        inputs.get("metric_id") or inputs.get("id"), artifact_data
                    )
                ]
            ),
            None,
            [],
        )
    if op == "metadata.series":
        return (
            "explore",
            "Explored series",
            _lines_nonempty(
                [_label_or_id(inputs.get("series_id") or inputs.get("id"), artifact_data)]
            ),
            None,
            [],
        )
    if op == "metadata.release":
        return (
            "explore",
            "Explored release",
            _lines_nonempty(
                [_short_id(inputs.get("release_id") or inputs.get("id"))]
            ),
            None,
            [],
        )
    if op == "entity.relationships":
        rel = inputs.get("relationship") or inputs.get("relation") or "relationships"
        return (
            "explore",
            "Explored relationships",
            _lines_nonempty(
                [
                    _label_or_id(inputs.get("entity_id") or inputs.get("id"), artifact_data),
                    str(rel),
                ]
            ),
            _count_stats(artifact_data, noun="links"),
            [],
        )
    if op in {"taxonomy.list", "taxonomy.entities"}:
        return (
            "explore",
            "Explored taxonomy",
            _lines_nonempty(
                [
                    str(inputs.get("taxonomy") or inputs.get("query") or "Taxonomies"),
                ]
            ),
            _count_stats(artifact_data, noun="items"),
            [],
        )
    if op == "query.run":
        return _query_run_body(inputs, artifact_data)
    if op == "query.inspect":
        kind, title, lines, stats, preview = _query_run_body(inputs, artifact_data)
        return kind, "Inspected query data", lines, stats, preview
    if op == "coverage.preview":
        return (
            "retrieve",
            "Previewed coverage",
            _lines_nonempty([_subject_from_inputs(op, inputs, artifact_data)]),
            _count_stats(artifact_data, noun="series"),
            [],
        )
    if op == "query.add":
        q = _artifact_query(artifact_data) or {}
        name = (q.get("name") if isinstance(q, dict) else None) or inputs.get("name")
        return (
            "mutate",
            "Created query",
            _lines_nonempty([str(name) if name else "Untitled query"]),
            None,
            [],
        )
    if op == "query.patch":
        return _query_patch_body(inputs, artifact_data)
    if op == "query.remove":
        name = None
        if isinstance(artifact_data, dict):
            name = artifact_data.get("name")
        return (
            "mutate",
            "Removed query",
            _lines_nonempty([str(name) if name else f"Query {inputs.get('query_id', '')}"]),
            None,
            [],
        )
    if op == "remark.add":
        return _remark_add_body(inputs, artifact_data)
    if op == "remark.remove":
        removed = (
            artifact_data.get("removed") if isinstance(artifact_data, dict) else None
        )
        title_bit = None
        if isinstance(removed, dict):
            title_bit = removed.get("name") or (removed.get("text") or "")[:48]
        return (
            "remark",
            "Removed research remark",
            _lines_nonempty([str(title_bit) if title_bit else None]),
            None,
            [],
        )
    if op == "workspace.add":
        return (
            "mutate",
            "Added to workspace",
            _workspace_lines(inputs, artifact_data),
            None,
            [],
        )
    if op == "workspace.rm":
        return (
            "mutate",
            "Removed from workspace",
            _workspace_lines(inputs, artifact_data),
            None,
            [],
        )
    if op == "annotate":
        return (
            "remark",
            "Added annotation",
            _lines_nonempty(
                [
                    str(inputs.get("text") or inputs.get("body") or "")[:80] or None,
                ]
            ),
            None,
            [],
        )
    if op.startswith("curation."):
        return (
            "mutate",
            "Updated publication",
            _lines_nonempty([op.replace("curation.", "").replace("_", " ")]),
            None,
            [],
        )
    # Fallback: machine op with light subject.
    return (
        "other",
        op.replace(".", " ").replace("_", " ").title(),
        _lines_nonempty([_subject_from_inputs(op, inputs, artifact_data)]),
        None,
        [],
    )


def _search_body(
    title: str,
    inputs: dict[str, Any],
    artifact_data: Any,
) -> tuple[EventKind, str, list[str], Optional[str], list[PreviewRow]]:
    q = inputs.get("query")
    lines = _lines_nonempty([str(q).strip() if q else None])
    tax = inputs.get("taxonomy")
    if tax:
        lines.append(f"taxonomy {tax}")
    stats = _count_stats(artifact_data, noun="results")
    return "search", title, lines, stats, []


def _query_run_body(
    inputs: dict[str, Any],
    artifact_data: Any,
) -> tuple[EventKind, str, list[str], Optional[str], list[PreviewRow]]:
    query = _artifact_query(artifact_data)
    if not isinstance(query, dict):
        query = {}
    metrics = list(query.get("metric") or inputs.get("metric") or [])
    entities = list(query.get("entity") or inputs.get("entity") or [])
    series = list(query.get("series") or inputs.get("series") or [])
    records = []
    if isinstance(artifact_data, dict):
        records = list(artifact_data.get("records") or [])

    economic = bool(series) and not metrics and not entities
    title = "Retrieved economic data" if economic else "Retrieved data"

    subject = _query_subject(metrics, entities, series, records, query)
    lines = _lines_nonempty([subject])

    freq = query.get("frequency") or inputs.get("frequency")
    span = _observation_span(records)
    scope_bits: list[str] = []
    if freq:
        scope_bits.append(str(freq).replace("_", " ").title())
    if span:
        scope_bits.append(span)
    elif query.get("tail"):
        scope_bits.append(f"last {query['tail']}")
    elif query.get("head"):
        scope_bits.append(f"first {query['head']}")
    if scope_bits:
        lines.append(" · ".join(scope_bits))

    n_obs = _observation_count(records)
    n_series = (
        int(artifact_data.get("record_count"))
        if isinstance(artifact_data, dict) and artifact_data.get("record_count") is not None
        else len(records)
    )
    source = _dominant_source(records)
    stats_bits: list[str] = []
    if n_obs:
        stats_bits.append(f"{n_obs} observation{'s' if n_obs != 1 else ''}")
    elif n_series:
        stats_bits.append(f"{n_series} series")
    if source:
        stats_bits.append(source)
    stats = " · ".join(stats_bits) if stats_bits else None

    preview = _preview_from_records(records, limit=5)
    return "retrieve", title, lines, stats, preview


def _query_patch_body(
    inputs: dict[str, Any],
    artifact_data: Any,
) -> tuple[EventKind, str, list[str], Optional[str], list[PreviewRow]]:
    before = artifact_data.get("before") if isinstance(artifact_data, dict) else None
    after = _artifact_query(artifact_data)
    adds: list[str] = []
    removes: list[str] = []
    for mid in inputs.get("add_metric") or []:
        adds.append(f"+ {_short_id(mid)}")
    for eid in inputs.get("add_entity") or []:
        adds.append(f"+ {_short_id(eid)}")
    for sid in inputs.get("add_series") or []:
        adds.append(f"+ {_short_id(sid)}")
    for mid in inputs.get("remove_metric") or []:
        removes.append(f"− {_short_id(mid)}")
    for eid in inputs.get("remove_entity") or []:
        removes.append(f"− {_short_id(eid)}")
    for sid in inputs.get("remove_series") or []:
        removes.append(f"− {_short_id(sid)}")

    # Prefer before/after diff when inputs omit add lists.
    if not adds and not removes and isinstance(before, dict) and isinstance(after, dict):
        for field_name in ("metric", "entity", "series"):
            b = set(before.get(field_name) or [])
            a = set(after.get(field_name) or [])
            for x in sorted(a - b):
                adds.append(f"+ {_short_id(x)}")
            for x in sorted(b - a):
                removes.append(f"− {_short_id(x)}")

    lines = adds[:6] + removes[:6]
    if inputs.get("name"):
        lines.insert(0, f"Renamed → {inputs['name']}")
    if inputs.get("frequency") is not None:
        lines.append(f"Frequency {inputs['frequency']}")
    if not lines:
        name = None
        if isinstance(after, dict):
            name = after.get("name")
        lines = _lines_nonempty([str(name) if name else "Query updated"])

    title = "Expanded query" if adds and not removes else "Updated query"
    return "mutate", title, lines, None, []


def _remark_add_body(
    inputs: dict[str, Any],
    artifact_data: Any,
) -> tuple[EventKind, str, list[str], Optional[str], list[PreviewRow]]:
    remark = artifact_data.get("remark") if isinstance(artifact_data, dict) else None
    if not isinstance(remark, dict):
        remark = {}
    name = remark.get("name") or inputs.get("name")
    text = (remark.get("text") or inputs.get("text") or "").strip()
    lines = _lines_nonempty(
        [
            str(name) if name else (text[:72] + ("…" if len(text) > 72 else "") or None),
        ]
    )
    if name and text and text != name:
        lines.append(text[:90] + ("…" if len(text) > 90 else ""))
    cites = list(remark.get("series") or inputs.get("series") or [])
    stats = None
    if cites:
        stats = f"{len(cites)} supporting citation{'s' if len(cites) != 1 else ''}"
    return "remark", "Added research remark", lines, stats, []


def _workspace_lines(inputs: dict[str, Any], artifact_data: Any) -> list[str]:
    bits: list[str] = []
    for key, label in (
        ("metric", "metric"),
        ("entity", "entity"),
        ("series", "series"),
    ):
        vals = inputs.get(key) or inputs.get(f"{key}_id")
        if isinstance(vals, (list, tuple)):
            for v in vals[:3]:
                bits.append(f"{label} {_short_id(v)}")
        elif vals:
            bits.append(f"{label} {_short_id(vals)}")
    if not bits and isinstance(artifact_data, dict):
        bits.append("Workspace updated")
    return _lines_nonempty(bits)


def _query_subject(
    metrics: list[Any],
    entities: list[Any],
    series: list[Any],
    records: list[dict[str, Any]],
    query: dict[str, Any],
) -> Optional[str]:
    name = query.get("name")
    metric_labels: list[str] = []
    entity_labels: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        mid = rec.get("metric_id")
        lab = rec.get("label") or ""
        if mid and mid not in metric_labels:
            # Prefer short metric-ish name from label ("Revenues of X" → keep full once).
            metric_labels.append(str(mid) if not lab else str(mid))
        for ent in rec.get("entities") or []:
            if isinstance(ent, dict):
                el = ent.get("label") or ent.get("id")
            else:
                el = ent
            if el and el not in entity_labels:
                entity_labels.append(str(el))

    # Prefer human labels from entities on records; fall back to ids.
    if not entity_labels:
        entity_labels = [_short_id(e) for e in entities[:4]]

    metric_bit = None
    if metrics:
        # Use first metric id shortened; if single record label is clean, use it.
        if len(records) == 1 and isinstance(records[0], dict) and records[0].get("label"):
            metric_bit = str(records[0]["label"])
        else:
            metric_bit = ", ".join(_short_id(m) for m in metrics[:3])
    elif series:
        if len(records) == 1 and isinstance(records[0], dict) and records[0].get("label"):
            metric_bit = str(records[0]["label"])
        else:
            metric_bit = ", ".join(_short_id(s) for s in series[:2])
    elif name:
        metric_bit = str(name)

    if metric_bit and entity_labels:
        n = len(entities) or len(entity_labels)
        if n > 1:
            return f"{metric_bit} · {n} entities"
        return f"{metric_bit} · {entity_labels[0]}"
    if metric_bit:
        return metric_bit
    if entity_labels:
        return ", ".join(entity_labels[:3])
    return str(name) if name else None


def _preview_from_records(
    records: Sequence[Any], *, limit: int = 5
) -> list[PreviewRow]:
    rows: list[PreviewRow] = []
    for rec in records:
        if len(rows) >= limit:
            break
        if not isinstance(rec, dict):
            continue
        label = str(rec.get("label") or _short_id(rec.get("id")) or "Series")
        # Prefer entity label when present for multi-entity packs.
        ents = rec.get("entities") or []
        if ents and isinstance(ents[0], dict) and ents[0].get("label"):
            label = str(ents[0]["label"])
        elif ents and not isinstance(ents[0], dict):
            label = _short_id(ents[0])
        obs_list = rec.get("observations") or []
        if not obs_list:
            continue
        # Latest observation by timestamp.
        latest = obs_list[-1]
        if not isinstance(latest, dict):
            continue
        period = _period_label(
            latest.get("observation_timestamp") or latest.get("date"),
            rec.get("frequency"),
        )
        value = _format_value(latest.get("value"), rec.get("units"))
        rows.append(PreviewRow(label=label, period=period, value=value))
    return rows


def _observation_count(records: Sequence[Any]) -> int:
    total = 0
    for rec in records:
        if isinstance(rec, dict):
            total += len(rec.get("observations") or [])
    return total


def _observation_span(records: Sequence[Any]) -> Optional[str]:
    stamps: list[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for obs in rec.get("observations") or []:
            if isinstance(obs, dict):
                ts = obs.get("observation_timestamp") or obs.get("date")
                if ts:
                    stamps.append(str(ts)[:10])
    if not stamps:
        return None
    stamps.sort()
    lo, hi = stamps[0], stamps[-1]
    if lo[:4] == hi[:4]:
        return f"{lo[:4]}" if lo == hi else f"{lo[:4]}–{hi[:4]}"
    return f"{lo[:4]}–{hi[:4]}"


def _dominant_source(records: Sequence[Any]) -> Optional[str]:
    counts: dict[str, int] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        src = rec.get("source")
        if not src:
            continue
        key = str(src)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    best = max(counts, key=counts.get)
    # Pretty common sources.
    pretty = {
        "edgar": "SEC EDGAR",
        "fred": "FRED",
        "bls": "BLS",
        "bea": "BEA",
    }
    low = best.lower()
    for k, v in pretty.items():
        if k in low:
            return v
    return best


def _period_label(stamp: Any, frequency: Any) -> str:
    if stamp is None:
        return "—"
    text = str(stamp)
    day = text[:10]
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return day
    freq = (str(frequency or "")).lower()
    if "quarter" in freq or freq in {"q", "quarterly"}:
        q = (d.month - 1) // 3 + 1
        return f"{d.year} Q{q}"
    if "annual" in freq or "year" in freq or freq in {"a", "yearly"}:
        return str(d.year)
    if "month" in freq or freq in {"m", "monthly"}:
        return d.strftime("%b %Y")
    return day


def _format_value(value: Any, units: Any = None) -> str:
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_n = abs(num)
    unit = str(units or "").lower()
    money = "usd" in unit or "dollar" in unit or unit in {"$", "usd"}
    if abs_n >= 1_000_000_000:
        body = f"{num / 1_000_000_000:.1f}B"
    elif abs_n >= 1_000_000:
        body = f"{num / 1_000_000:.1f}M"
    elif abs_n >= 10_000:
        body = f"{num:,.0f}"
    elif abs_n >= 1:
        body = f"{num:,.2f}".rstrip("0").rstrip(".")
    else:
        body = f"{num:.4g}"
    return f"${body}" if money else body


def _artifact_query(artifact_data: Any) -> Optional[dict[str, Any]]:
    if isinstance(artifact_data, dict):
        q = artifact_data.get("query")
        if isinstance(q, dict):
            return q
    return None


def _count_stats(artifact_data: Any, *, noun: str) -> Optional[str]:
    n: Optional[int] = None
    if isinstance(artifact_data, list):
        n = len(artifact_data)
    elif isinstance(artifact_data, dict):
        if "record_count" in artifact_data:
            try:
                n = int(artifact_data["record_count"])
            except (TypeError, ValueError):
                n = None
        elif "results" in artifact_data and isinstance(
            artifact_data["results"], list
        ):
            n = len(artifact_data["results"])
        elif "items" in artifact_data and isinstance(artifact_data["items"], list):
            n = len(artifact_data["items"])
        elif "data" in artifact_data and isinstance(artifact_data["data"], list):
            n = len(artifact_data["data"])
    if n is None:
        return None
    return f"{n} {noun}"


def _label_or_id(value: Any, artifact_data: Any) -> Optional[str]:
    if value is None and isinstance(artifact_data, dict):
        lab = artifact_data.get("label") or artifact_data.get("name")
        if lab:
            return str(lab)
        value = artifact_data.get("id")
    if value is None:
        return None
    if isinstance(artifact_data, dict):
        lab = artifact_data.get("label") or artifact_data.get("name")
        if lab and (
            artifact_data.get("id") == value or not artifact_data.get("id")
        ):
            return str(lab)
    if isinstance(artifact_data, list) and artifact_data:
        first = artifact_data[0]
        if isinstance(first, dict) and first.get("label"):
            # Single-result metadata style.
            if len(artifact_data) == 1:
                return str(first["label"])
    return _short_id(value)


def _subject_from_inputs(
    op: str, inputs: dict[str, Any], artifact_data: Any
) -> Optional[str]:
    for key in (
        "query",
        "name",
        "entity_id",
        "metric_id",
        "series_id",
        "id",
        "text",
    ):
        if inputs.get(key):
            return _label_or_id(inputs.get(key), artifact_data)
    return None


def _short_id(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    if len(text) <= 40:
        return text
    # Keep a readable tail for long series ids.
    return text[:18] + "…" + text[-12:]


def _lines_nonempty(parts: Sequence[Optional[str]]) -> list[str]:
    out: list[str] = []
    for p in parts:
        if p is None:
            continue
        s = str(p).strip()
        if s:
            out.append(s)
    return out
