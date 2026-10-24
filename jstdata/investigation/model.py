"""Investigation domain: workspace, queries, actions, and the durable artifact."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Union

if TYPE_CHECKING:
    import pandas as pd

    from ..client import JSTDataClient
    from ..models import TimeSeries

PathLike = Union[str, Path]

ROOT_ACTION_ID = 0
DEFAULT_WORKSPACE_ID = 0


class InvestigationError(ValueError):
    """Invalid investigation structure or mutation."""


@dataclass
class Workspace:
    """Staged analytical intent inside an investigation.

    Mirrors ``JSTDataClient.query`` / ``jst api query`` parameters. No
    observations — only resource ids and filters. Ids are numerical;
    ``label`` / ``description`` are TUI conveniences.
    """

    id: int = DEFAULT_WORKSPACE_ID
    label: Optional[str] = None
    description: Optional[str] = None
    metric: list[str] = field(default_factory=list)
    entity: list[str] = field(default_factory=list)
    series: list[str] = field(default_factory=list)
    frequency: Optional[str] = None
    taxonomy: Optional[str] = None
    head: Optional[int] = None
    tail: Optional[int] = None
    as_of: Optional[str] = None
    sort_by: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    order_by: Optional[str] = None

    def is_empty(self) -> bool:
        """True when the workspace has no resource selectors."""
        return not (self.metric or self.entity or self.series)

    def resource_ids(self) -> list[str]:
        """All metric, entity, and series IDs in the workspace."""
        return [*self.metric, *self.entity, *self.series]

    def to_query_kwargs(self) -> dict[str, Any]:
        """kwargs suitable for ``JSTDataClient.query`` / ``query_df``.

        Date/time fields are ignored: ``/query`` is head/tail/as_of only.
        """
        kwargs: dict[str, Any] = {}
        if self.metric:
            kwargs["metric"] = list(self.metric)
        if self.entity:
            kwargs["entity"] = list(self.entity)
        if self.series:
            kwargs["series"] = list(self.series)
        if self.frequency is not None:
            kwargs["frequency"] = self.frequency
        if self.taxonomy is not None:
            kwargs["taxonomy"] = self.taxonomy
        if self.head is not None:
            kwargs["head"] = self.head
        if self.tail is not None:
            kwargs["tail"] = self.tail
        if self.as_of is not None:
            kwargs["as_of"] = self.as_of
        if self.sort_by is not None:
            kwargs["sort_by"] = self.sort_by
        if self.order_by is not None:
            kwargs["order_by"] = self.order_by
        return kwargs

    def add_metric(self, resource_id: str) -> bool:
        """Append a metric ID if not already present. Returns True if added."""
        if resource_id in self.metric:
            return False
        self.metric.append(resource_id)
        return True

    def add_entity(self, resource_id: str) -> bool:
        """Append an entity ID if not already present. Returns True if added."""
        if resource_id in self.entity:
            return False
        self.entity.append(resource_id)
        return True

    def add_series(self, resource_id: str) -> bool:
        """Append a series ID if not already present. Returns True if added."""
        if resource_id in self.series:
            return False
        self.series.append(resource_id)
        return True

    def remove_id(self, resource_id: str) -> bool:
        """Remove an ID from metric/entity/series lists. Returns True if removed."""
        removed = False
        for collection in (self.metric, self.entity, self.series):
            while resource_id in collection:
                collection.remove(resource_id)
                removed = True
        return removed

    def execute(
        self, client: JSTDataClient, **overrides: Any
    ) -> List[TimeSeries]:
        """Run this workspace against ``client.query``."""
        kwargs = self.to_query_kwargs()
        kwargs.update(overrides)
        return client.query(**kwargs)

    def execute_df(self, client: JSTDataClient, **overrides: Any) -> pd.DataFrame:
        """Run this workspace and return a flattened DataFrame."""
        kwargs = self.to_query_kwargs()
        kwargs.update(overrides)
        return client.query_df(**kwargs)

    def to_cli(self, program: str = "jst") -> str:
        """Render a reproducible ``jst api query`` invocation."""
        parts = [program, "api", "query"]
        for m in self.metric:
            parts.append(f"--metric {m}")
        for e in self.entity:
            parts.append(f"--entity {e}")
        for s in self.series:
            parts.append(f"--series {s}")
        if self.frequency:
            parts.append(f"--frequency {self.frequency}")
        if self.taxonomy:
            parts.append(f"--taxonomy {self.taxonomy}")
        if self.head is not None:
            parts.append(f"--head {self.head}")
        if self.tail is not None:
            parts.append(f"--tail {self.tail}")
        if self.as_of:
            parts.append(f"--as-of {self.as_of}")
        if self.sort_by:
            parts.append(f"--sort-by {self.sort_by}")
        return " ".join(parts)

    def to_python(self) -> str:
        """Render a reproducible Python snippet for this workspace."""
        kwargs = self.to_query_kwargs()
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
            "print(df)"
        )

    def to_csv(
        self,
        client: JSTDataClient,
        path: Optional[PathLike] = None,
        **overrides: Any,
    ) -> Path:
        """Execute this workspace and write observations to a CSV file."""
        results = self.execute(client, **overrides)
        out = Path(
            path
            if path is not None
            else f"jst_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )

        with open(out, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["DATE", "LABEL", "VALUE", "UNITS", "SOURCE"])
            for ts in results:
                for obs in ts.observations:
                    writer.writerow(
                        [
                            obs.observation_timestamp.strftime("%Y-%m-%d"),
                            ts.series.label,
                            obs.value,
                            ts.series.units,
                            ts.series.source,
                        ]
                    )
        return out

    def resource_fingerprint(self) -> tuple:
        """Comparable snapshot of staged resources + filters."""
        return (
            tuple(self.metric),
            tuple(self.entity),
            tuple(self.series),
            self.frequency,
            self.taxonomy,
            self.head,
            self.tail,
            self.as_of,
            self.sort_by,
            self.start_date,
            self.end_date,
            self.start_time,
            self.end_time,
            self.order_by,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Workspace:
        if not isinstance(data, Mapping):
            raise InvestigationError("Workspace must be a mapping.")
        raw_id = data.get("id", DEFAULT_WORKSPACE_ID)
        try:
            wid = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise InvestigationError("Workspace 'id' must be an integer.") from exc
        return cls(
            id=wid,
            label=data.get("label"),
            description=data.get("description"),
            metric=list(data.get("metric") or []),
            entity=list(data.get("entity") or []),
            series=list(data.get("series") or []),
            frequency=data.get("frequency"),
            taxonomy=data.get("taxonomy"),
            head=data.get("head"),
            tail=data.get("tail"),
            as_of=data.get("as_of"),
            sort_by=data.get("sort_by"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            order_by=data.get("order_by"),
        )


@dataclass
class Query:
    """A saved ``/query`` spec. Metric × entity is a cartesian product.

    Optional ``units`` / ``source`` / ``seasonal_adjustment`` are persisted for
    later filtering; ``execute`` currently forwards the fields ``GET /query``
    already accepts.
    """

    id: int
    name: Optional[str] = None
    notes: Optional[str] = None
    metric: list[str] = field(default_factory=list)
    entity: list[str] = field(default_factory=list)
    series: list[str] = field(default_factory=list)
    frequency: Optional[str] = None
    units: Optional[str] = None
    source: Optional[str] = None
    seasonal_adjustment: Optional[str] = None
    taxonomy: Optional[str] = None
    head: Optional[int] = None
    tail: Optional[int] = None
    as_of: Optional[str] = None
    sort_by: Optional[str] = None
    order_by: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.metric or self.entity or self.series)

    def to_query_kwargs(self) -> dict[str, Any]:
        """kwargs for ``JSTDataClient.query`` (cartesian metric × entity)."""
        kwargs: dict[str, Any] = {}
        if self.metric:
            kwargs["metric"] = list(self.metric)
        if self.entity:
            kwargs["entity"] = list(self.entity)
        if self.series:
            kwargs["series"] = list(self.series)
        if self.frequency is not None:
            kwargs["frequency"] = self.frequency
        if self.taxonomy is not None:
            kwargs["taxonomy"] = self.taxonomy
        if self.head is not None:
            kwargs["head"] = self.head
        if self.tail is not None:
            kwargs["tail"] = self.tail
        if self.as_of is not None:
            kwargs["as_of"] = self.as_of
        if self.sort_by is not None:
            kwargs["sort_by"] = self.sort_by
        if self.order_by is not None:
            kwargs["order_by"] = self.order_by
        return kwargs

    def execute(self, client: JSTDataClient, **overrides: Any) -> List[TimeSeries]:
        kwargs = self.to_query_kwargs()
        kwargs.update(overrides)
        return client.query(**kwargs)

    def execute_df(self, client: JSTDataClient, **overrides: Any) -> pd.DataFrame:
        kwargs = self.to_query_kwargs()
        kwargs.update(overrides)
        return client.query_df(**kwargs)

    def expected_pairs(self) -> list[tuple[str, str]]:
        """Cartesian metric × entity pairs this query asks the API for."""
        return [(m, e) for m in self.metric for e in self.entity]

    def inspect(
        self, client: JSTDataClient, *, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        """Coverage for the cartesian product — no observations.

        Uses ``GET /preview`` when both metric and entity lists are set.
        """
        expected = [
            {"metric": m, "entity": e} for m, e in self.expected_pairs()
        ]
        resolved: list[dict[str, Any]] = []
        if self.metric and self.entity:
            rows = client.preview(
                metric=list(self.metric),
                entity=list(self.entity),
                limit=limit,
                offset=offset,
            )
            for row in rows:
                earliest = getattr(row, "earliest", None)
                latest = getattr(row, "latest", None)
                resolved.append(
                    {
                        "metric": row.metric_id,
                        "entity": row.entity_id,
                        "series_count": row.series_count,
                        "observation_count": row.observation_count,
                        "earliest": (
                            earliest.isoformat()
                            if hasattr(earliest, "isoformat")
                            else earliest
                        ),
                        "latest": (
                            latest.isoformat()
                            if hasattr(latest, "isoformat")
                            else latest
                        ),
                    }
                )
        found = {(item["metric"], item["entity"]) for item in resolved}
        missing = [
            pair
            for pair in expected
            if (pair["metric"], pair["entity"]) not in found
        ]
        return {
            "query": self.to_dict(),
            "cartesian": True,
            "expected_pairs": expected,
            "resolved": resolved,
            "missing": missing,
            "series": list(self.series),
            "counts": {
                "expected_pairs": len(expected),
                "resolved": len(resolved),
                "missing": len(missing),
                "series": len(self.series),
            },
        }

    @classmethod
    def from_workspace(
        cls,
        workspace: Workspace,
        *,
        query_id: int,
        name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Query:
        """Snapshot a workspace basket into a saved query."""
        return cls(
            id=query_id,
            name=name,
            notes=notes,
            metric=list(workspace.metric),
            entity=list(workspace.entity),
            series=list(workspace.series),
            frequency=workspace.frequency,
            taxonomy=workspace.taxonomy,
            head=workspace.head,
            tail=workspace.tail,
            as_of=workspace.as_of,
            sort_by=workspace.sort_by,
            order_by=workspace.order_by,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None and v != []}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Query:
        if not isinstance(data, Mapping):
            raise InvestigationError("Query must be a mapping.")
        try:
            qid = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationError("Query requires an integer 'id'.") from exc
        metric = data.get("metric") or []
        entity = data.get("entity") or []
        series = data.get("series") or []
        if not isinstance(metric, list) or not all(isinstance(x, str) for x in metric):
            raise InvestigationError(f"Query {qid} metric must be a list of strings.")
        if not isinstance(entity, list) or not all(isinstance(x, str) for x in entity):
            raise InvestigationError(f"Query {qid} entity must be a list of strings.")
        if not isinstance(series, list) or not all(isinstance(x, str) for x in series):
            raise InvestigationError(f"Query {qid} series must be a list of strings.")
        return cls(
            id=qid,
            name=data.get("name"),
            notes=data.get("notes"),
            metric=list(metric),
            entity=list(entity),
            series=list(series),
            frequency=data.get("frequency"),
            units=data.get("units"),
            source=data.get("source"),
            seasonal_adjustment=data.get("seasonal_adjustment"),
            taxonomy=data.get("taxonomy"),
            head=data.get("head"),
            tail=data.get("tail"),
            as_of=data.get("as_of"),
            sort_by=data.get("sort_by"),
            order_by=data.get("order_by"),
        )


@dataclass
class ResearchContext:
    """Analytical material a View is about (Input Context / overrides).

    The investigation workspace remains the global bag. A View may carry a
    ``context`` override that focus/replaces selected dimensions without
    mutating the global workspace.
    """

    metric: list[str] = field(default_factory=list)
    entity: list[str] = field(default_factory=list)
    series: list[str] = field(default_factory=list)
    release: list[str] = field(default_factory=list)
    taxonomy: Optional[str] = None

    def is_empty(self) -> bool:
        return not (
            self.metric
            or self.entity
            or self.series
            or self.release
            or self.taxonomy
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.metric:
            data["metric"] = list(self.metric)
        if self.entity:
            data["entity"] = list(self.entity)
        if self.series:
            data["series"] = list(self.series)
        if self.release:
            data["release"] = list(self.release)
        if self.taxonomy is not None:
            data["taxonomy"] = self.taxonomy
        return data

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> Optional[ResearchContext]:
        if data is None:
            return None
        if not isinstance(data, Mapping):
            raise InvestigationError("context must be a mapping.")
        metric = data.get("metric") or []
        entity = data.get("entity") or []
        series = data.get("series") or []
        release = data.get("release") or []
        if not isinstance(metric, list) or not all(isinstance(x, str) for x in metric):
            raise InvestigationError("context.metric must be a list of strings.")
        if not isinstance(entity, list) or not all(isinstance(x, str) for x in entity):
            raise InvestigationError("context.entity must be a list of strings.")
        if not isinstance(series, list) or not all(isinstance(x, str) for x in series):
            raise InvestigationError("context.series must be a list of strings.")
        if not isinstance(release, list) or not all(isinstance(x, str) for x in release):
            raise InvestigationError("context.release must be a list of strings.")
        taxonomy = data.get("taxonomy")
        if taxonomy is not None and not isinstance(taxonomy, str):
            raise InvestigationError("context.taxonomy must be a string or null.")
        ctx = cls(
            metric=list(metric),
            entity=list(entity),
            series=list(series),
            release=list(release),
            taxonomy=taxonomy,
        )
        return None if ctx.is_empty() else ctx

    @classmethod
    def from_workspace(cls, workspace: Workspace) -> ResearchContext:
        """Global context snapshot from a workspace bag."""
        return cls(
            metric=list(workspace.metric),
            entity=list(workspace.entity),
            series=list(workspace.series),
            taxonomy=workspace.taxonomy,
        )


def resolve_input_context(
    workspace: Workspace,
    overrides: Optional[ResearchContext] = None,
) -> ResearchContext:
    """Merge global workspace with View-local overrides (focus/replace).

    Non-empty override lists replace that dimension; empty means inherit.
    ``taxonomy`` replaces when the override object sets it (including when the
    override is present with taxonomy=None only if explicitly stored — v1:
    inherit unless override.taxonomy is a non-None string, or override has
    entity/metric focus without taxonomy and we still inherit taxonomy).
    """
    base = ResearchContext.from_workspace(workspace)
    if overrides is None:
        return base
    return ResearchContext(
        metric=list(overrides.metric) if overrides.metric else list(base.metric),
        entity=list(overrides.entity) if overrides.entity else list(base.entity),
        series=list(overrides.series) if overrides.series else list(base.series),
        # Releases are view-local only (not on the workspace bag).
        release=list(overrides.release) if overrides.release else list(base.release),
        taxonomy=(
            overrides.taxonomy if overrides.taxonomy is not None else base.taxonomy
        ),
    )


def focus_replace_context(
    parent: Optional[ResearchContext],
    *,
    metric: Optional[list[str]] = None,
    entity: Optional[list[str]] = None,
    series: Optional[list[str]] = None,
    release: Optional[list[str]] = None,
) -> ResearchContext:
    """Child overrides: replace named dimensions, keep parent's other overrides."""
    base = parent or ResearchContext()
    return ResearchContext(
        metric=list(metric) if metric is not None else list(base.metric),
        entity=list(entity) if entity is not None else list(base.entity),
        series=list(series) if series is not None else list(base.series),
        release=list(release) if release is not None else list(base.release),
        taxonomy=base.taxonomy,
    )


@dataclass
class Action:
    """One View (or legacy action) node in the investigation tree.

    ``context`` holds optional Input Context overrides (focus/replace) relative
    to the active workspace. Absent/null means inherit the full global bag.
    """

    id: int
    type: str
    parent_id: Optional[int] = None
    description: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    context: Optional[ResearchContext] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "args": dict(self.args),
        }
        if self.parent_id is not None:
            data["parent_id"] = self.parent_id
        if self.description is not None:
            data["description"] = self.description
        if self.context is not None and not self.context.is_empty():
            data["context"] = self.context.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Action:
        if not isinstance(data, Mapping):
            raise InvestigationError("Action must be a mapping.")
        try:
            aid = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationError("Action requires an integer 'id'.") from exc
        action_type = data.get("type")
        if not action_type or not isinstance(action_type, str):
            raise InvestigationError(f"Action {aid} requires a string 'type'.")
        parent_raw = data.get("parent_id", None)
        parent_id: Optional[int]
        if parent_raw is None:
            parent_id = None
        else:
            try:
                parent_id = int(parent_raw)
            except (TypeError, ValueError) as exc:
                raise InvestigationError(
                    f"Action {aid} parent_id must be an integer or null."
                ) from exc
        args = data.get("args") or {}
        if not isinstance(args, dict):
            raise InvestigationError(f"Action {aid} args must be a mapping.")
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            raise InvestigationError(f"Action {aid} description must be a string.")
        try:
            context = ResearchContext.from_dict(data.get("context"))
        except InvestigationError as exc:
            raise InvestigationError(f"Action {aid}: {exc}") from exc
        return cls(
            id=aid,
            type=action_type,
            parent_id=parent_id,
            description=description,
            args=dict(args),
            context=context,
        )


@dataclass
class Investigation:
    """Persistent mutable research artifact."""

    id: str
    title: str
    notes: Optional[str] = None
    workspace: Workspace = field(default_factory=Workspace)
    queries: dict[int, Query] = field(default_factory=dict)
    actions: dict[int, Action] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise if the investigation tree or queries are inconsistent."""
        if ROOT_ACTION_ID not in self.actions:
            raise InvestigationError(
                f"Investigation {self.id!r} must include root action "
                f"{ROOT_ACTION_ID}."
            )
        root = self.actions[ROOT_ACTION_ID]
        if root.parent_id is not None:
            raise InvestigationError("Root action must have parent_id: null.")

        roots = [a for a in self.actions.values() if a.parent_id is None]
        if len(roots) != 1:
            raise InvestigationError(
                f"Investigation {self.id!r} must have exactly one root action."
            )

        for action in self.actions.values():
            if action.id == ROOT_ACTION_ID:
                continue
            if action.parent_id is None:
                raise InvestigationError(
                    f"Action {action.id} must have a parent_id."
                )
            if action.parent_id not in self.actions:
                raise InvestigationError(
                    f"Action {action.id} parent_id {action.parent_id} "
                    "does not exist."
                )

        # Detect cycles via ancestor walk.
        for action in self.actions.values():
            seen: set[int] = set()
            cur: Optional[int] = action.id
            while cur is not None:
                if cur in seen:
                    raise InvestigationError(
                        f"Cycle detected in action tree at {cur}."
                    )
                seen.add(cur)
                parent = self.actions[cur].parent_id
                cur = parent

        for qid, query in self.queries.items():
            if query.id != qid:
                raise InvestigationError(
                    f"Query id mismatch: keyed as {qid}, object id {query.id}."
                )

    def children(self, action_id: int) -> list[Action]:
        """Direct children of ``action_id``, ordered by id ascending."""
        kids = [a for a in self.actions.values() if a.parent_id == action_id]
        kids.sort(key=lambda a: a.id)
        return kids

    def siblings(self, action_id: int) -> list[Action]:
        """Actions that share ``action_id``'s parent (including itself), by id.

        The root is alone in its sibling group.
        """
        action = self.get_action(action_id)
        if action.parent_id is None:
            return [action]
        return self.children(action.parent_id)

    def sibling_ids(self, action_id: int) -> list[int]:
        """Ids of ``siblings(action_id)``."""
        return [a.id for a in self.siblings(action_id)]

    def bfs_order(self) -> list[int]:
        """Breadth-first traversal ids starting at the root."""
        self.validate()
        order: list[int] = []
        queue = [ROOT_ACTION_ID]
        while queue:
            current = queue.pop(0)
            order.append(current)
            queue.extend(c.id for c in self.children(current))
        return order

    def workspace_order(self) -> list[int]:
        """Single workspace — kept so host navigation stays a no-op cycle."""
        return [DEFAULT_WORKSPACE_ID]

    def next_action_id(self) -> int:
        if not self.actions:
            return ROOT_ACTION_ID
        return max(self.actions) + 1

    def next_query_id(self) -> int:
        if not self.queries:
            return 1
        return max(self.queries) + 1

    def subtree_ids(self, action_id: int) -> list[int]:
        """``action_id`` and all descendants (BFS)."""
        if action_id not in self.actions:
            raise InvestigationError(f"Unknown action {action_id}.")
        result: list[int] = []
        queue = [action_id]
        while queue:
            current = queue.pop(0)
            result.append(current)
            queue.extend(c.id for c in self.children(current))
        return result

    def remove_action_cascade(self, action_id: int) -> list[int]:
        """Remove an action and all descendants. Returns removed ids.

        Cannot remove the root.
        """
        if action_id == ROOT_ACTION_ID:
            raise InvestigationError("Cannot remove the root action.")
        removed = self.subtree_ids(action_id)
        for rid in removed:
            del self.actions[rid]
        return removed

    def add_action(
        self,
        action_type: str,
        *,
        parent_id: int,
        args: Optional[dict[str, Any]] = None,
        description: Optional[str] = None,
        context: Optional[ResearchContext] = None,
        action_id: Optional[int] = None,
    ) -> Action:
        """Append a child action/view under ``parent_id``."""
        if parent_id not in self.actions:
            raise InvestigationError(f"Unknown parent action {parent_id}.")
        aid = self.next_action_id() if action_id is None else int(action_id)
        if aid in self.actions:
            raise InvestigationError(f"Action id {aid} already exists.")
        if aid == ROOT_ACTION_ID:
            raise InvestigationError("Cannot add another root action.")
        action = Action(
            id=aid,
            type=action_type,
            parent_id=parent_id,
            description=description,
            args=dict(args or {}),
            context=context,
        )
        self.actions[aid] = action
        self.validate()
        return action

    def add_query(
        self,
        *,
        name: Optional[str] = None,
        notes: Optional[str] = None,
        metric: Optional[list[str]] = None,
        entity: Optional[list[str]] = None,
        series: Optional[list[str]] = None,
        frequency: Optional[str] = None,
        units: Optional[str] = None,
        source: Optional[str] = None,
        seasonal_adjustment: Optional[str] = None,
        taxonomy: Optional[str] = None,
        head: Optional[int] = None,
        tail: Optional[int] = None,
        as_of: Optional[str] = None,
        sort_by: Optional[str] = None,
        order_by: Optional[str] = None,
        query_id: Optional[int] = None,
        from_workspace: bool = False,
    ) -> Query:
        """Append a saved query. Ids start at 1."""
        qid = self.next_query_id() if query_id is None else int(query_id)
        if qid in self.queries:
            raise InvestigationError(f"Query id {qid} already exists.")
        if from_workspace:
            query = Query.from_workspace(
                self.workspace, query_id=qid, name=name, notes=notes
            )
        else:
            query = Query(
                id=qid,
                name=name,
                notes=notes,
                metric=list(metric or []),
                entity=list(entity or []),
                series=list(series or []),
                frequency=frequency,
                units=units,
                source=source,
                seasonal_adjustment=seasonal_adjustment,
                taxonomy=taxonomy,
                head=head,
                tail=tail,
                as_of=as_of,
                sort_by=sort_by,
                order_by=order_by,
            )
        if query.is_empty():
            raise InvestigationError(
                "Query needs at least one metric, entity, or series."
            )
        self.queries[qid] = query
        return query

    def get_workspace(self, workspace_id: Optional[int] = None) -> Workspace:
        """Return the investigation's single workspace (id is ignored)."""
        return self.workspace

    def get_query(self, query_id: int) -> Query:
        try:
            return self.queries[query_id]
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown query {query_id} in investigation {self.id!r}."
            ) from exc

    def get_action(self, action_id: int) -> Action:
        try:
            return self.actions[action_id]
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown action {action_id} in investigation {self.id!r}."
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
        }
        if self.notes:
            data["notes"] = self.notes
        data["workspace"] = self.workspace.to_dict()
        if self.queries:
            data["queries"] = [
                self.queries[k].to_dict() for k in sorted(self.queries)
            ]
        data["actions"] = [self.actions[k].to_dict() for k in sorted(self.actions)]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Investigation:
        if not isinstance(data, Mapping):
            raise InvestigationError("Investigation file must be a mapping.")
        inv_id = data.get("id")
        if not inv_id or not isinstance(inv_id, str):
            raise InvestigationError("Investigation is missing a string 'id'.")
        title = data.get("title")
        if title is None:
            title = inv_id
        if not isinstance(title, str):
            raise InvestigationError("Investigation 'title' must be a string.")
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise InvestigationError("Investigation 'notes' must be a string.")

        workspace = _load_workspace(data, inv_id)
        queries = _load_queries(data, inv_id)

        raw_actions = data.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise InvestigationError(
                f"Investigation {inv_id!r} must list at least one action."
            )
        actions: dict[int, Action] = {}
        for item in raw_actions:
            action = Action.from_dict(item)
            if action.id in actions:
                raise InvestigationError(f"Duplicate action id {action.id}.")
            actions[action.id] = action

        inv = cls(
            id=inv_id,
            title=title,
            notes=notes,
            workspace=workspace,
            queries=queries,
            actions=actions,
        )
        inv.validate()
        return inv

    @classmethod
    def new(
        cls,
        investigation_id: str,
        *,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        root_type: str = "home",
        root_description: Optional[str] = None,
        root_args: Optional[dict[str, Any]] = None,
        workspace_label: Optional[str] = None,
        workspace_description: Optional[str] = None,
    ) -> Investigation:
        """Create a minimal valid investigation (workspace + root action 0)."""
        inv = cls(
            id=investigation_id,
            title=title if title is not None else investigation_id,
            notes=notes,
            workspace=Workspace(
                id=DEFAULT_WORKSPACE_ID,
                label=workspace_label,
                description=workspace_description,
            ),
            queries={},
            actions={
                ROOT_ACTION_ID: Action(
                    id=ROOT_ACTION_ID,
                    type=root_type,
                    parent_id=None,
                    description=root_description,
                    args=dict(root_args or {}),
                )
            },
        )
        inv.validate()
        return inv


def _load_workspace(data: Mapping[str, Any], inv_id: str) -> Workspace:
    """Accept singular ``workspace`` or legacy ``workspaces:`` list (first / id 0)."""
    if "workspace" in data and data["workspace"] is not None:
        raw = data["workspace"]
        if not isinstance(raw, Mapping):
            raise InvestigationError(
                f"Investigation {inv_id!r} workspace must be a mapping."
            )
        return Workspace.from_dict(raw)

    raw_list = data.get("workspaces")
    if isinstance(raw_list, list) and raw_list:
        by_id: dict[int, Workspace] = {}
        first: Optional[Workspace] = None
        for item in raw_list:
            ws = Workspace.from_dict(item)
            if first is None:
                first = ws
            by_id[ws.id] = ws
        if DEFAULT_WORKSPACE_ID in by_id:
            return by_id[DEFAULT_WORKSPACE_ID]
        assert first is not None
        return first

    return Workspace()


def _load_queries(data: Mapping[str, Any], inv_id: str) -> dict[int, Query]:
    raw = data.get("queries") or []
    if not isinstance(raw, list):
        raise InvestigationError(
            f"Investigation {inv_id!r} queries must be a list."
        )
    queries: dict[int, Query] = {}
    for item in raw:
        query = Query.from_dict(item)
        if query.id in queries:
            raise InvestigationError(f"Duplicate query id {query.id}.")
        queries[query.id] = query
    return queries
