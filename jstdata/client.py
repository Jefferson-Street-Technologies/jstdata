import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import requests

from .models import (
    Entity,
    EntityRelationship,
    Metric,
    MetricTaxonomy,
    Observation,
    PreviewRow,
    Release,
    Series,
    TimeSeries,
    Resource,
    Taxonomy,
)

APP_DIR = Path.home() / ".jstdata"
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_QUERY_TAIL = 20
DEFAULT_QUERY_SERIES_LIMIT = 50
MAX_QUERY_SERIES_LIMIT = 50
MAX_QUERY_OBSERVATION_DEPTH = 100
# API ``GET /resource`` rejects more than this many ids in one request.
MAX_RESOURCE_IDS = 30


class ApiKeyNotSetError(Exception):
    pass


class InvalidApiKeyError(Exception):
    pass


def _as_id_list(value: Optional[Union[str, List[str]]]) -> Optional[List[str]]:
    """Normalize a search/query id argument to a non-empty list."""
    if value is None:
        return None
    if isinstance(value, str):
        return [value] if value else None
    items = [v for v in value if v]
    return items or None


def _format_as_of(value: Union[str, datetime]) -> str:
    """ISO-8601 timestamp; naive datetimes are treated as UTC."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


class InvalidInputError(Exception):
    pass


@dataclass
class JSTDataClientConfig:
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    def __post_init__(self):
        APP_DIR.mkdir(exist_ok=True)

        # 1. Start with defaults
        default_url = "https://api.jeffersonst.io"

        # 2. Layer on config file if it exists
        file_api_key = None
        file_base_url = None
        if CONFIG_FILE.exists():
            try:
                cfg = _read_config_file(CONFIG_FILE)
                file_api_key = cfg.get("api_key")
                file_base_url = cfg.get("base_url")
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        # Precedence: Env > Arg > File > Default
        # self.api_key and self.base_url contain 'Arg' if passed, else None.

        self.api_key = os.environ.get("JSTDATA_API_KEY") or self.api_key or file_api_key
        self.base_url = (
            os.environ.get("JSTDATA_BASE_URL")
            or self.base_url
            or file_base_url
            or default_url
        )

    def write(self, **kwargs) -> None:
        """Write configuration to the config file."""
        # Read current to preserve keys we aren't updating
        current: Dict[str, Any] = {}
        if CONFIG_FILE.exists():
            try:
                current = _read_config_file(CONFIG_FILE)
            except Exception:
                pass

        current["api_key"] = kwargs.get("api_key") or current.get("api_key") or self.api_key
        current["base_url"] = (
            kwargs.get("base_url") or current.get("base_url") or self.base_url
        )

        with open(CONFIG_FILE, "w") as f:
            json.dump(current, f, indent=2)

        # Restrict permissions to owner read/write
        CONFIG_FILE.chmod(0o600)

    def read(self) -> Dict[str, Any]:
        """Read the current configuration from file."""
        if not CONFIG_FILE.exists():
            return {}
        return _read_config_file(CONFIG_FILE)


def _strip_json_line_comments(text: str) -> str:
    """Remove ``//`` comments that appear outside of JSON strings."""
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            # Skip through end of line.
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _read_config_file(path: Path) -> Dict[str, Any]:
    """Load config JSON, tolerating ``//`` line comments outside strings."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(_strip_json_line_comments(text))
    if not isinstance(data, dict):
        raise ValueError("config root must be an object")
    return data


@dataclass
class JSTDataCache:
    endpoint: str
    params: Optional[Dict[str, Any]]

    def __post_init__(self):
        if self.params is None:
            self.params = {}

        # Normalize params for hashing
        sorted_items = sorted(
            [(k, str(v)) for k, v in self.params.items() if v is not None]
        )
        self.params = dict(sorted_items)

        self._cache_dir = APP_DIR / "cache"
        self._cache_dir.mkdir(exist_ok=True)

        json_data = json.dumps(
            {
                "endpoint": self.endpoint,
                "params": self.params,
            }
        )

        key = hashlib.sha256(json_data.encode()).hexdigest()
        self._cache_file = self._cache_dir / f"{key}.parquet"

    def read(self):
        if not self._cache_file.exists():
            return None
        return pd.read_parquet(self._cache_file)

    def write(self, df: pd.DataFrame):
        df.to_parquet(self._cache_file)


class JSTDataClient:
    def __init__(
        self, api_key: Optional[str] = None, base_url: Optional[str] = None
    ):
        """
        Initializes the JSTDataClient.

        Args:
            api_key: The API key for authenticating with the Jefferson Street REST API.
            base_url: The base URL of the API.
        """
        # Pass non-None values to override defaults/config/env
        kwargs = {}
        if api_key: kwargs["api_key"] = api_key
        if base_url: kwargs["base_url"] = base_url
        self._cfg = JSTDataClientConfig(**kwargs)

    @property
    def api_key(self):
        if not self._cfg.api_key:
            raise ApiKeyNotSetError("API key is not set. Run 'jstdata login' or set JSTDATA_API_KEY.")
        return self._cfg.api_key

    @property
    def base_url(self):
        return self._cfg.base_url

    def validate_key(self, api_key: Optional[str] = None) -> bool:
        """
        Validates the API key by making a lightweight request.
        """
        original_key = self._cfg.api_key
        if api_key:
            self._cfg.api_key = api_key
        
        try:
            # Simple lightweight request to verify the key
            self.make_request("metric", params={"limit": 1})
            return True
        except InvalidApiKeyError:
            return False
        except Exception:
            raise
        finally:
            self._cfg.api_key = original_key

    def make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        enable_cache: bool = False,
    ) -> Dict[str, Any]:
        """
        Low-level method to make a request to the API.
        """
        if endpoint[0] != "/":
            endpoint = f"/{endpoint}"

        url = f"{self.base_url}{endpoint}"

        # Caching logic could be more sophisticated, but keeping it simple for now
        if enable_cache:
            cache = JSTDataCache(endpoint, params)
            cached_df = cache.read()
            if cached_df is not None:
                return {"records": cached_df.to_dict("records")}

        api_key = self.api_key
        with requests.Session() as session:
            session.params = {"api-key": api_key}
            response = session.get(url, params=params)

        if response.status_code == 403:
            raise InvalidApiKeyError("Invalid API key")
        response.raise_for_status()

        data = response.json()
        if enable_cache and "records" in data:
            cache.write(pd.DataFrame(data["records"]))

        return data

    # --- Metrics ---

    def list_metrics(
        self, limit: int = 100, offset: int = 0, sort_order: str = "asc"
    ) -> List[Metric]:
        """List all available metrics."""
        data = self.make_request(
            "metric", {"limit": limit, "offset": offset, "sort_order": sort_order}
        )
        return [Metric.from_dict(m) for m in data["records"]]

    def get_metric(self, metric_id: str) -> Metric:
        """Get details for a specific metric."""
        data = self.make_request(f"metric/{metric_id}")
        return Metric.from_dict(data)

    def get_metric_series(
        self, metric_id: str, limit: int = 100, offset: int = 0
    ) -> List[Series]:
        """Get all series associated with a metric."""
        data = self.make_request(f"metric/{metric_id}/series", {"limit": limit, "offset": offset})
        return [Series.from_dict(s) for s in data["records"]]

    def get_metric_taxonomies(
        self, metric_id: str, limit: int = 100, offset: int = 0
    ) -> List[MetricTaxonomy]:
        """Taxonomic coverage for a metric (``GET /metric/{id}/taxonomies``)."""
        data = self.make_request(
            f"metric/{metric_id}/taxonomies",
            {"limit": limit, "offset": offset},
        )
        return [MetricTaxonomy.from_dict(t) for t in data["records"]]

    # --- Releases ---

    def list_releases(
        self, limit: int = 100, offset: int = 0, sort_order: str = "asc"
    ) -> List[Release]:
        """List releases / filings."""
        data = self.make_request(
            "release",
            {"limit": limit, "offset": offset, "sort_order": sort_order},
        )
        return [Release.from_dict(r) for r in data["records"]]

    def get_release(self, release_id: str) -> Release:
        """Get a single release / filing by id."""
        data = self.make_request(f"release/{release_id}")
        return Release.from_dict(data)

    # --- Preview ---

    def preview(
        self,
        metric: Optional[Union[str, List[str]]] = None,
        entity: Optional[Union[str, List[str]]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PreviewRow]:
        """Cheap metric×entity coverage (``GET /preview``).

        Only pairs that have at least one series are returned. Clients should
        cache responses — this is billed per returned row.
        """
        metrics = _as_id_list(metric)
        entities = _as_id_list(entity)
        if not metrics or not entities:
            raise InvalidInputError("preview requires at least one metric and one entity.")
        params: Dict[str, Any] = {
            "metric": metrics,
            "entity": entities,
            "limit": limit,
            "offset": offset,
        }
        data = self.make_request("preview", params)
        return [PreviewRow.from_dict(r) for r in data.get("records", [])]

    # --- Series ---

    def list_series(
        self, limit: int = 100, offset: int = 0, sort_order: str = "asc"
    ) -> List[Series]:
        """List all available series."""
        data = self.make_request(
            "series", {"limit": limit, "offset": offset, "sort_order": sort_order}
        )
        return [Series.from_dict(s) for s in data["records"]]

    def get_series(self, series_id: str) -> Series:
        """Get details for a specific series."""
        data = self.make_request(f"series/{series_id}")
        return Series.from_dict(data)

    # --- Entities ---

    def get_entity(self, entity_id: str) -> Entity:
        """Get details for a specific entity."""
        data = self.make_request(f"entity/{entity_id}")
        return Entity.from_dict(data)

    def get_entity_series(
        self, entity_id: str, limit: int = 100, offset: int = 0
    ) -> List[Series]:
        """Get all series associated with an entity."""
        data = self.make_request(f"entity/{entity_id}/series", {"limit": limit, "offset": offset})
        return [Series.from_dict(s) for s in data["records"]]

    def get_entity_relations(
        self, entity_id: str, limit: int = 100, offset: int = 0
    ) -> List[EntityRelationship]:
        """Get relationships for an entity (the graph view)."""
        data = self.make_request(
            f"entity/{entity_id}/relations", {"limit": limit, "offset": offset}
        )
        return [EntityRelationship.from_dict(r) for r in data["records"]]

    # --- Taxonomies ---

    def list_taxonomies(
        self, limit: int = 100, offset: int = 0, sort_order: str = "asc"
    ) -> List[Taxonomy]:
        """List taxonomies that have identity (membership) relationships."""
        data = self.make_request(
            "taxonomy", {"limit": limit, "offset": offset, "sort_order": sort_order}
        )
        return [Taxonomy.from_dict(t) for t in data["records"]]

    def get_taxonomy(self, taxonomy_id: str) -> Taxonomy:
        """Get details for a specific taxonomy."""
        data = self.make_request(f"taxonomy/{taxonomy_id}")
        return Taxonomy.from_dict(data)

    def get_taxonomy_entities(
        self, taxonomy_id: str, limit: int = 100, offset: int = 0
    ) -> List[Entity]:
        """List entities that themselves participate in a taxonomy."""
        data = self.make_request(
            f"taxonomy/{taxonomy_id}/entities", {"limit": limit, "offset": offset}
        )
        return [Entity.from_dict(e) for e in data["records"]]

    def get_taxonomy_metrics(
        self, taxonomy_id: str, limit: int = 100, offset: int = 0
    ) -> List[Metric]:
        """List metrics with series on entities in a taxonomy."""
        data = self.make_request(
            f"taxonomy/{taxonomy_id}/metrics", {"limit": limit, "offset": offset}
        )
        return [Metric.from_dict(m) for m in data["records"]]

    # --- Search ---

    def search(
        self, query: str, limit: int = 15, offset: int = 0
    ) -> List[Union[Entity, Metric, Series]]:
        """Unified search across all resource types."""
        data = self.make_request(
            "search", {"query": query, "limit": limit, "offset": offset}
        )
        results = []
        for r in data["records"]:
            res_type = r.get("type")
            if res_type == "entity":
                results.append(Entity.from_dict(r))
            elif res_type == "metric":
                results.append(Metric.from_dict(r))
            elif res_type == "series":
                results.append(Series.from_dict(r))
        return results

    def search_entities(
        self,
        query: Optional[str] = None,
        metric: Optional[Union[str, List[str]]] = None,
        taxonomy: Optional[str] = None,
        relation: Optional[Union[str, List[str]]] = None,
        mode: str = "union",
        limit: int = 5,
        offset: int = 0,
    ) -> List[Entity]:
        """Search or list entities.

        Omit ``query`` (or pass blank) to list the matching set in label order.
        ``metric`` may be one id or many; ``mode`` is ``union`` or ``intersect``
        when more than one metric is given.
        ``relation`` filters to entities with a typed edge to an anchor
        (``<relationship_type>:<to_entity_id>``). Multiple relations are OR'd.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query is not None and str(query).strip():
            params["query"] = query
        metrics = _as_id_list(metric)
        if metrics:
            params["metric"] = metrics
            params["mode"] = mode
        if taxonomy:
            params["taxonomy"] = taxonomy
        relations = _as_id_list(relation)
        if relations:
            params["relation"] = relations
        data = self.make_request("search/entities", params)
        return [Entity.from_dict(e) for e in data["records"]]

    def search_metrics(
        self,
        query: Optional[str] = None,
        entity: Optional[Union[str, List[str]]] = None,
        taxonomy: Optional[str] = None,
        mode: str = "union",
        limit: int = 5,
        offset: int = 0,
    ) -> List[Metric]:
        """Search or list metrics.

        Omit ``query`` (or pass blank) to list the matching set in name order.
        ``entity`` may be one id or many; ``mode`` is ``union`` or ``intersect``
        when more than one entity is given.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if query is not None and str(query).strip():
            params["query"] = query
        entities = _as_id_list(entity)
        if entities:
            params["entity"] = entities
            params["mode"] = mode
        if taxonomy:
            params["taxonomy"] = taxonomy
        data = self.make_request("search/metrics", params)
        return [Metric.from_dict(m) for m in data["records"]]

    def search_series(
        self, query: str, limit: int = 5, offset: int = 0
    ) -> List[Series]:
        """Search for series."""
        data = self.make_request(
            "search/series", {"query": query, "limit": limit, "offset": offset}
        )
        return [Series.from_dict(s) for s in data["records"]]

    # --- Query ---

    def query(
        self,
        metric: Optional[Union[str, List[str]]] = None,
        entity: Optional[Union[str, List[str]]] = None,
        series: Optional[Union[str, List[str]]] = None,
        frequency: Optional[str] = None,
        taxonomy: Optional[str] = None,
        release_id: Optional[Union[str, List[str]]] = None,
        head: Optional[int] = None,
        tail: Optional[int] = None,
        as_of: Optional[Union[str, datetime]] = None,
        sort_by: Optional[str] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[TimeSeries]:
        """Bounded cross-sectional observations (``GET /query``).

        Exactly one of ``head`` or ``tail`` is sent. If neither is given,
        ``tail`` defaults to ``DEFAULT_QUERY_TAIL``. Time ranges are not
        accepted here; use :meth:`get_series_observations` for history.
        ``limit`` / ``offset`` paginate series, not observations.

        ``sort_by`` is ``id`` (default catalog order) or ``value`` (rank by
        the chronologically last observation in each series' window,
        descending). ``taxonomy`` restricts to series whose entities have an
        identity relation to that taxonomy. ``release_id`` filters to one or
        more provenance releases.
        """
        if head is not None and tail is not None:
            raise InvalidInputError("Provide exactly one of 'head' or 'tail'.")
        if head is None and tail is None:
            tail = DEFAULT_QUERY_TAIL
        if sort_by is not None and sort_by not in ("id", "value"):
            raise InvalidInputError("'sort_by' must be 'id' or 'value'.")

        params: Dict[str, Any] = {
            "metric": metric,
            "entity": entity,
            "series": series,
            "frequency": frequency,
            "taxonomy": taxonomy,
            "release_id": _as_id_list(release_id),
            "head": head,
            "tail": tail,
            "sort_by": sort_by,
            "order_by": order_by,
            "limit": limit,
            "offset": offset,
        }
        if as_of is not None:
            params["as_of"] = _format_as_of(as_of)
        params = {k: v for k, v in params.items() if v is not None}

        data = self.make_request("query", params)
        return [TimeSeries.from_dict(record) for record in data.get("records", [])]

    def get_series_observations(
        self,
        series_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        release_id: Optional[Union[str, List[str]]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[Observation]:
        """Paginated history for one series (``GET /series/{id}/observations``)."""
        params: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "start_time": start_time,
            "end_time": end_time,
            "release_id": _as_id_list(release_id),
            "order_by": order_by,
            "limit": limit,
            "offset": offset,
        }
        params = {k: v for k, v in params.items() if v is not None}
        data = self.make_request(f"series/{series_id}/observations", params)
        sid = data.get("series_id", series_id)
        return [
            Observation.from_dict(dict(o, series_id=sid))
            for o in data.get("observations", [])
        ]

    def get_resources(self, resource: Union[str, List[str]]) -> List[Resource]:
        """Resolve labels for metric/entity/series ids (``GET /resource``).

        The API accepts at most ``MAX_RESOURCE_IDS`` ids per request; larger
        lists are fetched in batches.
        """
        ids = _as_id_list(resource) or []
        records: List[Resource] = []
        for i in range(0, len(ids), MAX_RESOURCE_IDS):
            chunk = ids[i : i + MAX_RESOURCE_IDS]
            data = self.make_request("resource", params={"resource": chunk})
            for r in data["records"]:
                records.append(Resource(id=r["id"], label=r["label"]))
        return records

    def query_df(self, **kwargs) -> pd.DataFrame:
        """Flatten ``query`` results to one row per observation."""
        results = self.query(**kwargs)
        rows: List[Dict[str, Any]] = []
        for ts in results:
            entity_id = ",".join(e.id for e in ts.series.entities)
            for obs in ts.observations:
                rows.append(
                    {
                        "series_id": ts.series.id,
                        "series_label": ts.series.label,
                        "metric_id": ts.series.metric_id,
                        "entity_id": entity_id,
                        "frequency": ts.series.frequency,
                        "units": ts.series.units,
                        "source": ts.series.source,
                        "observation_timestamp": obs.observation_timestamp,
                        "release_timestamp": obs.release_timestamp,
                        "release_id": obs.release_id,
                        "value": obs.value,
                    }
                )
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["observation_timestamp"] = pd.to_datetime(df["observation_timestamp"])
        df["release_timestamp"] = pd.to_datetime(df["release_timestamp"])
        return df
