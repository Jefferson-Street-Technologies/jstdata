"""JSON-friendly serialization for action-log artifacts."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Sequence


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return jsonable(value.dict())
    return {"repr": repr(value)}


def serialize_items(items: Sequence[Any]) -> list[Any]:
    return [jsonable(item) for item in items]


def serialize_timeseries(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """Flatten ``TimeSeries`` (series + observations) into JSON dicts."""
    out: list[dict[str, Any]] = []
    for row in rows:
        series = getattr(row, "series", None)
        observations = getattr(row, "observations", None)
        if series is None:
            out.append(jsonable(row))
            continue
        payload = jsonable(series)
        if not isinstance(payload, dict):
            payload = {"series": payload}
        payload["observations"] = jsonable(observations or [])
        out.append(payload)
    return out
