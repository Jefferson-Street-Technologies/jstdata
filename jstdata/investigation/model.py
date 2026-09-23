"""Investigation domain: workspaces, actions, and the durable artifact."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from ..session import Session

ROOT_ACTION_ID = 0
DEFAULT_WORKSPACE_ID = 0


class InvestigationError(ValueError):
    """Invalid investigation structure or mutation."""


@dataclass
class Workspace:
    """Investigation-scoped analytical state (formerly Session).

    Ids are numerical. ``label`` is a TUI convenience only.
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

    def to_session(self) -> Session:
        """Project workspace resource selectors into a Session for steps."""
        return Session(
            metric=list(self.metric),
            entity=list(self.entity),
            series=list(self.series),
            frequency=self.frequency,
            taxonomy=self.taxonomy,
            head=self.head,
            tail=self.tail,
            as_of=self.as_of,
            sort_by=self.sort_by,
            start_date=self.start_date,
            end_date=self.end_date,
            start_time=self.start_time,
            end_time=self.end_time,
            order_by=self.order_by,
        )

    def apply_session(self, session: Session) -> None:
        """Copy resource selectors from a live Session into this workspace."""
        self.metric = list(session.metric)
        self.entity = list(session.entity)
        self.series = list(session.series)
        self.frequency = session.frequency
        self.taxonomy = session.taxonomy
        self.head = session.head
        self.tail = session.tail
        self.as_of = session.as_of
        self.sort_by = session.sort_by
        self.start_date = session.start_date
        self.end_date = session.end_date
        self.start_time = session.start_time
        self.end_time = session.end_time
        self.order_by = session.order_by

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
        try:
            wid = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvestigationError("Workspace requires an integer 'id'.") from exc
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
class Action:
    """One use of a reusable step type within an investigation tree."""

    id: int
    type: str
    parent_id: Optional[int] = None
    description: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)

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
        return cls(
            id=aid,
            type=action_type,
            parent_id=parent_id,
            description=description,
            args=dict(args),
        )


@dataclass
class Investigation:
    """Persistent mutable research artifact."""

    id: str
    title: str
    workspaces: dict[int, Workspace] = field(default_factory=dict)
    actions: dict[int, Action] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise if the investigation tree or workspaces are inconsistent."""
        if not self.workspaces:
            raise InvestigationError(
                f"Investigation {self.id!r} must have at least one workspace."
            )
        if DEFAULT_WORKSPACE_ID not in self.workspaces:
            raise InvestigationError(
                f"Investigation {self.id!r} must include workspace "
                f"{DEFAULT_WORKSPACE_ID}."
            )
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

    def children(self, action_id: int) -> list[Action]:
        """Direct children of ``action_id``, ordered by id ascending."""
        kids = [a for a in self.actions.values() if a.parent_id == action_id]
        kids.sort(key=lambda a: a.id)
        return kids

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

    def next_action_id(self) -> int:
        if not self.actions:
            return ROOT_ACTION_ID
        return max(self.actions) + 1

    def next_workspace_id(self) -> int:
        if not self.workspaces:
            return DEFAULT_WORKSPACE_ID
        return max(self.workspaces) + 1

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
        action_id: Optional[int] = None,
    ) -> Action:
        """Append a child action under ``parent_id``."""
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
        )
        self.actions[aid] = action
        self.validate()
        return action

    def get_workspace(self, workspace_id: int) -> Workspace:
        try:
            return self.workspaces[workspace_id]
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown workspace {workspace_id} in investigation {self.id!r}."
            ) from exc

    def get_action(self, action_id: int) -> Action:
        try:
            return self.actions[action_id]
        except KeyError as exc:
            raise InvestigationError(
                f"Unknown action {action_id} in investigation {self.id!r}."
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "workspaces": [
                self.workspaces[k].to_dict() for k in sorted(self.workspaces)
            ],
            "actions": [self.actions[k].to_dict() for k in sorted(self.actions)],
        }

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

        raw_workspaces = data.get("workspaces")
        if not isinstance(raw_workspaces, list) or not raw_workspaces:
            raise InvestigationError(
                f"Investigation {inv_id!r} must list at least one workspace."
            )
        workspaces: dict[int, Workspace] = {}
        for item in raw_workspaces:
            ws = Workspace.from_dict(item)
            if ws.id in workspaces:
                raise InvestigationError(f"Duplicate workspace id {ws.id}.")
            workspaces[ws.id] = ws

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

        inv = cls(id=inv_id, title=title, workspaces=workspaces, actions=actions)
        inv.validate()
        return inv

    @classmethod
    def new(
        cls,
        investigation_id: str,
        *,
        title: Optional[str] = None,
        root_type: str = "home",
        root_description: Optional[str] = None,
        root_args: Optional[dict[str, Any]] = None,
        workspace_label: Optional[str] = None,
        workspace_description: Optional[str] = None,
    ) -> Investigation:
        """Create a minimal valid investigation (workspace 0 + root action 0)."""
        inv = cls(
            id=investigation_id,
            title=title if title is not None else investigation_id,
            workspaces={
                DEFAULT_WORKSPACE_ID: Workspace(
                    id=DEFAULT_WORKSPACE_ID,
                    label=workspace_label,
                    description=workspace_description,
                )
            },
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
