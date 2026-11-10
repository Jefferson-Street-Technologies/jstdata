"""Shared list pagination helpers (limit+1 sentinel for has_more)."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def page_fetch_limit(page_size: int) -> int:
    """Request one extra row so :func:`take_page` can detect another page."""
    return page_size + 1


def take_page(rows: Sequence[T], page_size: int) -> tuple[list[T], bool]:
    """Trim a ``page_size + 1`` fetch to ``page_size`` and report ``has_more``.

    Callers should request :func:`page_fetch_limit` rows, then pass the
    result here. Avoids a separate ``COUNT(*)`` / total field.
    """
    items = list(rows)
    if len(items) > page_size:
        return items[:page_size], True
    return items, False
