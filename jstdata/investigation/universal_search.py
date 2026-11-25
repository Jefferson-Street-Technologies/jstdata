"""Context-aware universal search modal (/)."""

from __future__ import annotations

from typing import Any, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..ops import discovery as disc

# Display order for the type strip (internal id → label).
_KIND_TABS: tuple[tuple[str, str], ...] = (
    ("all", "everything"),
    ("entity", "entity"),
    ("metric", "metric"),
)


class _Hit(ListItem):
    def __init__(self, kind: str, resource_id: str, label: str) -> None:
        self.kind = kind
        self.resource_id = resource_id
        self.label = label
        super().__init__(
            Label(f"{kind:<7}  {label}  [dim]{resource_id}[/]", markup=True)
        )


class UniversalSearchModal(ModalScreen[Optional[dict[str, Any]]]):
    """Search from anywhere; type strip picks everything / entity / metric."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("enter", "inspect", "Inspect result", show=True, priority=True),
        Binding("space", "collect", "Collect", show=False),
        Binding("ctrl+enter", "add_to_query", "Add to query", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("tab", "cycle_kind", "Type", show=True),
        Binding("1", "kind_all", "Everything", show=False),
        Binding("2", "kind_entity", "Entity", show=False),
        Binding("3", "kind_metric", "Metric", show=False),
    ]

    CSS = """
    UniversalSearchModal { align: center middle; }
    #usearch-panel {
        width: 80%;
        height: 70%;
        border: solid #4ade80;
        background: #0c0c0c;
        padding: 1;
    }
    #usearch-title { text-style: bold; color: #e0e0e0; height: 1; }
    #usearch-kinds {
        height: 1;
        margin: 1 0;
    }
    .usearch-kind {
        width: auto;
        margin-right: 2;
        color: #666;
        padding: 0 1;
    }
    .usearch-kind.-active {
        color: #0a0a0a;
        background: #4ade80;
        text-style: bold;
    }
    #usearch-hint { color: #666; height: 1; margin-bottom: 1; }
    #usearch-input { margin-bottom: 1; }
    #usearch-status { color: #888; height: 1; margin-bottom: 1; }
    #usearch-list { height: 1fr; }
    #usearch-list > ListItem.--highlight {
        background: #1a3a2a;
        border-left: solid #4ade80;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        *,
        context: str = "global",
        resource: str = "all",
        app_dir=None,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.context = context
        # Normalize: callers may pass "all" or constrained entity/metric.
        if resource not in {"all", "entity", "metric"}:
            resource = "all"
        self.resource = resource
        self.app_dir = app_dir
        self.project_id = project_id
        self.investigation_id = investigation_id
        self._hits: list[_Hit] = []
        self._searched = False

    def compose(self) -> ComposeResult:
        with Vertical(id="usearch-panel"):
            yield Static("SEARCH", id="usearch-title")
            with Horizontal(id="usearch-kinds"):
                for kind_id, label in _KIND_TABS:
                    shortcut = {"all": "1", "entity": "2", "metric": "3"}.get(
                        kind_id, ""
                    )
                    text = f"{label}  {shortcut}" if shortcut else label
                    yield Label(
                        text,
                        id=f"usearch-kind-{kind_id}",
                        classes="usearch-kind",
                    )
            yield Static(
                "Enter search · click type or 1–3 · Space collect · "
                "Ctrl+Enter add to query · Enter inspects",
                id="usearch-hint",
            )
            yield Input(
                placeholder="Type a query, then press Enter to search…",
                id="usearch-input",
            )
            yield Static("", id="usearch-status")
            yield ListView(id="usearch-list")
            yield Footer()

    def on_mount(self) -> None:
        self._refresh_kind_strip()
        self.query_one("#usearch-input", Input).focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Let the input own printable keys and Enter (search) while typing.
        if self._input_focused():
            if action in {
                "kind_all",
                "kind_entity",
                "kind_metric",
                "cursor_down",
                "cursor_up",
                "collect",
                "inspect",
            }:
                return False
        return True

    def _refresh_kind_strip(self) -> None:
        for kind_id, _label in _KIND_TABS:
            label = self.query_one(f"#usearch-kind-{kind_id}", Label)
            label.set_class(kind_id == self.resource, "-active")

    def _set_kind(self, kind: str) -> None:
        if kind not in {"all", "entity", "metric"}:
            return
        if kind == self.resource:
            return
        self.resource = kind
        self._refresh_kind_strip()
        # Re-run only if the user already searched once.
        if self._searched:
            value = self.query_one("#usearch-input", Input).value.strip()
            if value:
                self._search(value)

    @on(Click)
    def _kind_clicked(self, event: Click) -> None:
        widget = event.widget
        if widget is None:
            return
        wid = getattr(widget, "id", None) or ""
        if wid.startswith("usearch-kind-"):
            self._set_kind(wid.removeprefix("usearch-kind-"))
            event.stop()

    def _input_focused(self) -> bool:
        return (
            isinstance(self.focused, Input)
            and getattr(self.focused, "id", None) == "usearch-input"
        )

    @on(Input.Submitted, "#usearch-input")
    def _submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            self.query_one("#usearch-status", Static).update(
                "Type a query, then press Enter."
            )
            return
        self._search(query)

    @on(ListView.Selected, "#usearch-list")
    def _result_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _Hit):
            self._dismiss_inspect(item)

    def action_inspect(self) -> None:
        # Input owns Enter → search (see check_action + Input.Submitted).
        if self._input_focused():
            return
        hit = self._current()
        if hit:
            self._dismiss_inspect(hit)

    def _dismiss_inspect(self, hit: _Hit) -> None:
        self.dismiss(
            {
                "action": "inspect",
                "type": hit.kind,
                "id": hit.resource_id,
                "label": hit.label,
            }
        )

    @work(exclusive=True, group="usearch")
    async def _search(self, query: str) -> None:
        self._searched = True
        status = self.query_one("#usearch-status", Static)
        label = next(
            (lab for kid, lab in _KIND_TABS if kid == self.resource),
            "everything",
        )
        status.update(f"Searching {label} for “{query}”…")
        if len(query) < 1:
            self._set_hits([])
            status.update("")
            return
        kinds = (
            ["entity", "metric"]
            if self.resource == "all"
            else [self.resource]
        )
        hits: list[_Hit] = []
        for kind in kinds:
            try:
                if kind == "entity":
                    result = disc.search_entities(
                        self.client,
                        query,
                        limit=20,
                        project=self.project_id,
                        investigation=self.investigation_id,
                        app_dir=self.app_dir,
                    )
                elif kind == "metric":
                    result = disc.search_metrics(
                        self.client,
                        query,
                        limit=20,
                        project=self.project_id,
                        investigation=self.investigation_id,
                        app_dir=self.app_dir,
                    )
                else:
                    continue
            except Exception:  # noqa: BLE001
                continue
            rows = result.data if isinstance(result.data, list) else []
            for row in rows[:15]:
                if isinstance(row, dict):
                    rid = str(row.get("id") or "")
                    label_text = str(row.get("label") or rid)
                else:
                    rid = str(getattr(row, "id", ""))
                    label_text = str(getattr(row, "label", rid))
                if rid:
                    hits.append(_Hit(kind, rid, label_text))
        self._set_hits(hits)
        if hits:
            status.update(f"{len(hits)} result{'s' if len(hits) != 1 else ''}")
            self.query_one("#usearch-list", ListView).focus()
        else:
            status.update("No results")

    def _set_hits(self, hits: list[_Hit]) -> None:
        self._hits = hits
        listing = self.query_one("#usearch-list", ListView)
        listing.clear()
        for hit in hits:
            listing.append(hit)
        if hits:
            listing.index = 0

    def _current(self) -> Optional[_Hit]:
        listing = self.query_one("#usearch-list", ListView)
        item = listing.highlighted_child
        if isinstance(item, _Hit):
            return item
        idx = listing.index
        if idx is not None and 0 <= idx < len(self._hits):
            return self._hits[idx]
        return None

    def bug_report_snapshot(self) -> dict[str, Any]:
        try:
            q = self.query_one("#usearch-input", Input).value.strip()
        except Exception:  # noqa: BLE001
            q = ""
        kinds = (
            ["entity", "metric"]
            if self.resource == "all"
            else [self.resource]
        )
        return {
            "kind": "search",
            "query": q,
            "resource": self.resource,
            "searched": self._searched,
            "hit_count": len(self._hits),
            "hits": [
                {"type": h.kind, "id": h.resource_id, "label": h.label}
                for h in self._hits[:40]
            ],
            "api": {
                f"discovery.search_{kind}s": {
                    "q": q,
                    "limit": 20,
                    "project": self.project_id,
                    "investigation": self.investigation_id,
                }
                for kind in kinds
            },
        }

    def bug_report_summary(self) -> str:
        try:
            q = self.query_one("#usearch-input", Input).value.strip()
        except Exception:  # noqa: BLE001
            q = ""
        return (
            f"[dim]Search[/] [bold]{self.resource}[/] "
            f"[dim]“{q or '…'}” → search_entities / search_metrics[/]"
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_collect(self) -> None:
        hit = self._current()
        if not hit:
            return
        self.dismiss(
            {
                "action": "collect",
                "type": hit.kind,
                "id": hit.resource_id,
                "label": hit.label,
            }
        )

    def action_add_to_query(self) -> None:
        hit = self._current()
        if not hit:
            return
        self.dismiss(
            {
                "action": "add_to_query",
                "type": hit.kind,
                "id": hit.resource_id,
                "label": hit.label,
            }
        )

    def action_cursor_down(self) -> None:
        self.query_one("#usearch-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#usearch-list", ListView).action_cursor_up()

    def action_cycle_kind(self) -> None:
        cycle = [kid for kid, _ in _KIND_TABS]
        try:
            i = cycle.index(self.resource)
        except ValueError:
            i = 0
        self._set_kind(cycle[(i + 1) % len(cycle)])

    def action_kind_all(self) -> None:
        self._set_kind("all")

    def action_kind_entity(self) -> None:
        self._set_kind("entity")

    def action_kind_metric(self) -> None:
        self._set_kind("metric")
