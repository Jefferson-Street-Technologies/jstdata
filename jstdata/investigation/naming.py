"""Human-readable names for draft queries (no API / catalog changes)."""

from __future__ import annotations

import re
from typing import Mapping, Optional, Sequence

_AUTO_ENTITY_COUNT = re.compile(r" · \d+ entities$")
_AUTO_METRIC_COUNT = re.compile(r"^\d+ metrics · ")


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


def composition_subtitle(
    metric_ids: Sequence[str],
    entity_ids: Sequence[str],
    series_ids: Sequence[str] = (),
) -> str:
    """Live composition line: ``3 entities · 1 metric``."""
    bits: list[str] = []
    n_e = len([e for e in entity_ids if e])
    n_m = len([m for m in metric_ids if m])
    n_s = len([s for s in series_ids if s])
    if n_e:
        bits.append(f"{n_e} entit{'y' if n_e == 1 else 'ies'}")
    if n_m:
        bits.append(f"{n_m} metric{'s' if n_m != 1 else ''}")
    if n_s:
        bits.append(f"{n_s} series")
    return " · ".join(bits)


def looks_auto_query_name(
    name: Optional[str],
    metric_ids: Sequence[str],
    entity_ids: Sequence[str],
    *,
    labels: Optional[Mapping[str, str]] = None,
) -> bool:
    """True when ``name`` still looks machine-generated (safe to refresh)."""
    text = (name or "").strip()
    if not text or text == "Untitled query":
        return True
    suggested = suggest_query_name(metric_ids, entity_ids, labels=labels)
    if text == suggested:
        return True
    metrics = [m for m in metric_ids if m]
    if _AUTO_METRIC_COUNT.match(text):
        return True
    if _AUTO_ENTITY_COUNT.search(text):
        return True
    if len(metrics) == 1:
        metric_label = label_for(labels, metrics[0])
        if text == metric_label:
            return True
        if text.startswith(f"{metric_label} · "):
            return True
    return False


def sync_auto_query_name(
    name: Optional[str],
    metric_ids: Sequence[str],
    entity_ids: Sequence[str],
    *,
    labels: Optional[Mapping[str, str]] = None,
) -> str:
    """Return an updated name when the current one looks auto-generated."""
    suggested = suggest_query_name(metric_ids, entity_ids, labels=labels)
    if looks_auto_query_name(name, metric_ids, entity_ids, labels=labels):
        return suggested
    return (name or "").strip() or suggested
