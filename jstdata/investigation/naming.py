"""Human-readable names for draft queries (no API / catalog changes)."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence


def _humanize(resource_id: str) -> str:
    text = (resource_id or "").strip()
    if ":" in text:
        # cik:1588873 → keep as-is; labels should replace this when known.
        return text
    return text.replace("-", " ").replace("_", " ").strip().title()


def label_for(labels: Mapping[str, str] | None, resource_id: str) -> str:
    if labels:
        hit = labels.get(resource_id)
        if hit:
            return str(hit)
    return _humanize(resource_id)


def suggest_query_name(
    metric_ids: Sequence[str],
    entity_ids: Sequence[str],
    *,
    labels: Optional[Mapping[str, str]] = None,
) -> str:
    """Descriptive name for a new draft query.

    Examples:
    - one×one → ``Unemployment Rate · Michigan``
    - one×many → ``Unemployment Rate · 3 entities``
    - many×one → ``3 metrics · Michigan``
    """
    metrics = [m for m in metric_ids if m]
    entities = [e for e in entity_ids if e]
    if not metrics and not entities:
        return "Untitled query"
    if len(metrics) == 1 and len(entities) == 1:
        return (
            f"{label_for(labels, metrics[0])} · "
            f"{label_for(labels, entities[0])}"
        )
    if len(metrics) == 1:
        m = label_for(labels, metrics[0])
        if not entities:
            return m
        if len(entities) <= 3:
            return f"{m} · " + ", ".join(label_for(labels, e) for e in entities)
        return f"{m} · {len(entities)} entities"
    if len(entities) == 1:
        e = label_for(labels, entities[0])
        return f"{len(metrics)} metrics · {e}"
    return f"{len(metrics)} metrics · {len(entities)} entities"
