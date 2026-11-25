"""Reusable resource picker — search and select one catalog resource.

Used by query composition, chart compare, workspace management, and search.
Not query-editor-specific: callers scope by ``kind`` (entity | metric | series).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..ops import discovery as disc

ResourceKind = Literal["entity", "metric", "series"]

_KIND_TITLE = {
    "entity": "Add entity",
    "metric": "Add metric",
    "series": "Add series",
}


class _PickHit(ListItem):
    def __init__(self, kind: str, resource_id: str, label: str) -> None:
        self.kind = kind
        self.resource_id = resource_id
        self.label = label
        super().__init__(Label(f"{label}  [dim]{resource_id}[/]", markup=True))


class ResourcePickerModal(ModalScreen[Optional[dict[str, Any]]]):
    """Search catalog for one kind; Enter confirms, Esc cancels.

    Dismisses with ``{action: "pick", type, id, label}`` or ``None``.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("enter", "confirm", "Add", show=True, priority=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    CSS = """
    ResourcePickerModal { align: center middle; }
    #rp-panel {
        width: 64;
        height: 22;
        max-height: 80%;
        border: solid #4ade80;
        background: #0c0c0c;
        padding: 1 1;
    }
    #rp-title { text-style: bold; color: #e0e0e0; height: 1; margin-bottom: 1; }
    #rp-hint { color: #666; height: 1; margin-bottom: 1; }
    #rp-input { width: 100%; margin-bottom: 1; }
    #rp-status { color: #888; height: 1; margin-bottom: 1; }
    #rp-list { height: 1fr; }
    #rp-list > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        kind: ResourceKind,
        *,
        app_dir=None,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.kind: ResourceKind = kind if kind in {"entity", "metric", "series"} else "entity"
        self.app_dir = app_dir
        self.project_id = project_id
        self.investigation_id = investigation_id
        self._title = title or _KIND_TITLE.get(self.kind, "Add resource")
        self._hits: list[_PickHit] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="rp-panel"):
            yield Static(self._title, id="rp-title")
            yield Static("enter add · esc cancel", id="rp-hint")
            yield Input(placeholder="Search…", id="rp-input")
            yield Static("", id="rp-status")
            yield ListView(id="rp-list")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#rp-input", Input).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self._input_focused() and action in {
            "cursor_down",
            "cursor_up",
            "confirm",
        }:
            # Enter while typing → search (Input.Submitted); j/k move list after.
            if action == "confirm":
                return False
            return False
        return True

    def _input_focused(self) -> bool:
        return (
            isinstance(self.focused, Input)
            and getattr(self.focused, "id", None) == "rp-input"
        )

    @on(Input.Submitted, "#rp-input")
    def _submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            self.query_one("#rp-status", Static).update("Type a search, then press Enter.")
            return
        self._search(query)

    @on(ListView.Selected, "#rp-list")
    def _result_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _PickHit):
            self._pick(item)

    def action_confirm(self) -> None:
        if self._input_focused():
            return
        hit = self._current()
        if hit:
            self._pick(hit)

    def _pick(self, hit: _PickHit) -> None:
        self.dismiss(
            {
                "action": "pick",
                "type": hit.kind,
                "id": hit.resource_id,
                "label": hit.label,
            }
        )

    @work(exclusive=True, group="resource-picker")
    async def _search(self, query: str) -> None:
        import asyncio

        status = self.query_one("#rp-status", Static)
        status.update(f"Searching {self.kind}…")
        rows = await asyncio.to_thread(self._fetch_sync, query)
        hits = [_PickHit(self.kind, rid, label) for rid, label in rows]
        self._hits = hits
        listing = self.query_one("#rp-list", ListView)
        listing.clear()
        for hit in hits:
            listing.append(hit)
        if hits:
            listing.index = 0
            status.update(f"{len(hits)} result{'s' if len(hits) != 1 else ''}")
            listing.focus()
        else:
            status.update("No results")

    def _fetch_sync(self, query: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.kind == "entity":
            result = disc.search_entities(
                self.client,
                query,
                limit=20,
                project=self.project_id,
                investigation=self.investigation_id,
                app_dir=self.app_dir,
            )
            rows = result.data if isinstance(result.data, list) else []
        elif self.kind == "metric":
            result = disc.search_metrics(
                self.client,
                query,
                limit=20,
                project=self.project_id,
                investigation=self.investigation_id,
                app_dir=self.app_dir,
            )
            rows = result.data if isinstance(result.data, list) else []
        else:
            try:
                found = self.client.search_series(query, limit=20)
            except Exception:  # noqa: BLE001
                found = []
            rows = found if isinstance(found, list) else []
        for row in rows[:15]:
            if isinstance(row, dict):
                rid = str(row.get("id") or "")
                label = str(row.get("label") or row.get("name") or rid)
            else:
                rid = str(getattr(row, "id", "") or "")
                label = str(
                    getattr(row, "label", None)
                    or getattr(row, "name", None)
                    or rid
                )
            if rid:
                out.append((rid, label))
        return out

    def _current(self) -> Optional[_PickHit]:
        listing = self.query_one("#rp-list", ListView)
        item = listing.highlighted_child
        if isinstance(item, _PickHit):
            return item
        idx = listing.index
        if idx is not None and 0 <= idx < len(self._hits):
            return self._hits[idx]
        return None

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one("#rp-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#rp-list", ListView).action_cursor_up()
