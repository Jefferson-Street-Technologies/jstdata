"""Persistent label cache for workspace resource hydration.

Keys are ``{type}:{id}`` (entity / metric / series). Values store the
``GET /resource`` payload fields plus ``pulled_at``. When hydrating a
workspace, if any staged id is missing or older than ``CACHE_TTL``, the
entire workspace is refreshed in one batched ``/resource`` round-trip.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .client import APP_DIR, JSTDataClient
from .session import Session

CACHE_TTL = timedelta(hours=12)
DEFAULT_CACHE_PATH = APP_DIR / "cache" / "resources.json"


def cache_key(resource_type: str, resource_id: str) -> str:
    return f"{resource_type}:{resource_id}"


def _resource_type_for_id(session: Session, resource_id: str) -> str:
    if resource_id in session.metric:
        return "metric"
    if resource_id in session.entity:
        return "entity"
    if resource_id in session.series:
        return "series"
    return "series"


def _parse_pulled_at(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        # Support trailing Z
        text = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_fresh(entry: Mapping[str, Any], *, now: datetime) -> bool:
    pulled = _parse_pulled_at(entry.get("pulled_at"))
    if pulled is None:
        return False
    return now - pulled <= CACHE_TTL


class ResourceLabelCache:
    """JSON key-value cache for hydrated resource labels."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path if path is not None else DEFAULT_CACHE_PATH
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.is_file():
            self._data = {}
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = {}
            return
        if isinstance(raw, dict):
            self._data = {
                str(k): v for k, v in raw.items() if isinstance(v, dict)
            }
        else:
            self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(self.path)

    def get(self, resource_type: str, resource_id: str) -> Optional[dict[str, Any]]:
        self.load()
        return self._data.get(cache_key(resource_type, resource_id))

    def put_many(
        self,
        records: list[tuple[str, str, str]],
        *,
        pulled_at: Optional[datetime] = None,
    ) -> None:
        """Store ``(type, id, label)`` records with a shared pull timestamp."""
        self.load()
        stamp = (pulled_at or datetime.now(timezone.utc)).isoformat()
        for resource_type, resource_id, label in records:
            self._data[cache_key(resource_type, resource_id)] = {
                "type": resource_type,
                "id": resource_id,
                "label": label,
                "pulled_at": stamp,
            }
        self.save()

    def hydrate_session(
        self,
        client: JSTDataClient,
        session: Session,
        labels: dict[str, str],
        *,
        now: Optional[datetime] = None,
    ) -> None:
        """Fill ``labels`` for all session resource ids, refreshing if needed.

        If any staged resource is absent from the cache or older than
        ``CACHE_TTL``, all session ids are fetched via ``client.get_resources``
        and written back to the cache.
        """
        typed: list[tuple[str, str]] = []
        seen: set[str] = set()
        for rid in session.resource_ids():
            if rid in seen:
                continue
            seen.add(rid)
            typed.append((_resource_type_for_id(session, rid), rid))
        if not typed:
            return

        self.load()
        clock = now or datetime.now(timezone.utc)
        needs_refresh = False
        for resource_type, rid in typed:
            entry = self._data.get(cache_key(resource_type, rid))
            if entry is None or not _is_fresh(entry, now=clock):
                needs_refresh = True
                break

        if needs_refresh:
            ids = [rid for _, rid in typed]
            type_by_id = {rid: rtype for rtype, rid in typed}
            records: list[tuple[str, str, str]] = []
            for resource in client.get_resources(ids):
                rtype = type_by_id.get(resource.id, "series")
                label = resource.label or resource.id
                records.append((rtype, resource.id, label))
                labels[resource.id] = label
            # Ids the API omitted still get a fallback label and cache stamp
            # so we do not hammer forever on permanently missing ids.
            fetched = {r[1] for r in records}
            for rtype, rid in typed:
                if rid not in fetched:
                    labels.setdefault(rid, rid)
                    records.append((rtype, rid, rid))
            self.put_many(records, pulled_at=clock)
            return

        for resource_type, rid in typed:
            entry = self._data[cache_key(resource_type, rid)]
            labels[rid] = entry.get("label") or rid


_default_cache: Optional[ResourceLabelCache] = None


def default_resource_cache() -> ResourceLabelCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = ResourceLabelCache()
    return _default_cache


def reset_default_resource_cache_for_tests() -> None:
    """Drop the process-wide cache singleton (tests only)."""
    global _default_cache
    _default_cache = None
