"""Context-aware universal search modal (/)."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Click
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..ops import discovery as disc
from .search_history import load_search_history, remember_search

# Always-available type strip (internal id → label).
_KIND_BASE: tuple[tuple[str, str], ...] = (
    ("all", "everything"),
    ("entity", "entity"),
    ("metric", "metric"),
)

# Shown only when the open query has entities / metrics to scope against.
_KIND_SCOPED: tuple[tuple[str, str], ...] = (
    ("metrics_over_entities", "Metrics over Selected Entities"),
    ("entities_over_metrics", "Entities over Selected Metrics"),
)

_KIND_LABELS = dict(_KIND_BASE + _KIND_SCOPED)


class _Hit(ListItem):
    def __init__(self, kind: str, resource_id: str, label: str) -> None:
        self.kind = kind
        self.resource_id = resource_id
        self.label = label
        super().__init__(
            Label(f"{kind:<7}  {label}  [dim]{resource_id}[/]", markup=True)
        )


class _HistoryHit(ListItem):
    """A previous search in this project/report."""

    def __init__(self, query: str, resource: str) -> None:
        self.query = query
        self.resource = resource
        scope = _KIND_LABELS.get(resource, resource)
        super().__init__(
            Label(
                f"[dim]recent[/]  {query}  [dim]{scope}[/]",
                markup=True,
            )
        )


class UniversalSearchModal(ModalScreen[Optional[dict[str, Any]]]):
    """Search from anywhere; type strip picks scope / entity / metric."""

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
        Binding("1", "kind_1", "Kind 1", show=False),
        Binding("2", "kind_2", "Kind 2", show=False),
        Binding("3", "kind_3", "Kind 3", show=False),
        Binding("4", "kind_4", "Kind 4", show=False),
        Binding("5", "kind_5", "Kind 5", show=False),
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
        height: auto;
        min-height: 1;
        max-height: 3;
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
        selected_entities: Optional[Sequence[str]] = None,
        selected_metrics: Optional[Sequence[str]] = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.context = context
        self.app_dir = app_dir
        self.project_id = project_id
        self.investigation_id = investigation_id
        self.selected_entities = [e for e in (selected_entities or []) if e]
        self.selected_metrics = [m for m in (selected_metrics or []) if m]
        self._hits: list[_Hit] = []
        self._history: list[dict[str, str]] = []
        self._showing_history = False
        self._history_cursor = -1  # up/down recall index into _history
        self._searched = False
        self._kind_tabs = self._build_kind_tabs()
        if resource not in {kid for kid, _ in self._kind_tabs}:
            resource = "all"
        self.resource = resource

    def _build_kind_tabs(self) -> tuple[tuple[str, str], ...]:
        tabs: list[tuple[str, str]] = list(_KIND_BASE)
        if self.selected_entities:
            tabs.append(_KIND_SCOPED[0])
        if self.selected_metrics:
            tabs.append(_KIND_SCOPED[1])
        return tuple(tabs)

    def compose(self) -> ComposeResult:
        with Vertical(id="usearch-panel"):
            yield Static("SEARCH", id="usearch-title")
            with Horizontal(id="usearch-kinds"):
                for i, (kind_id, label) in enumerate(self._kind_tabs, start=1):
                    yield Label(
                        f"{label}  {i}",
                        id=f"usearch-kind-{kind_id}",
                        classes="usearch-kind",
                    )
            scoped_hint = ""
            if self.selected_entities or self.selected_metrics:
                scoped_hint = " · scoped tabs use the open query"
            yield Static(
                "Enter search · ↑/↓ history · click type or 1–"
                f"{len(self._kind_tabs)}{scoped_hint} · Space collect · "
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
        self._show_history()
        self.query_one("#usearch-input", Input).focus()

    def _show_history(self) -> None:
        self._history = load_search_history(
            self.project_id, app_dir=self.app_dir
        )
        self._history_cursor = -1
        listing = self.query_one("#usearch-list", ListView)
        listing.clear()
        self._hits = []
        self._showing_history = bool(self._history)
        status = self.query_one("#usearch-status", Static)
        if not self._history:
            status.update("No recent searches in this report yet")
            return
        for entry in self._history:
            listing.append(
                _HistoryHit(entry["query"], entry.get("resource") or "all")
            )
        listing.index = 0
        status.update(
            f"{len(self._history)} recent search"
            f"{'es' if len(self._history) != 1 else ''} in this report"
        )

    def _apply_history_entry(self, entry: dict[str, str] | _HistoryHit) -> None:
        if isinstance(entry, _HistoryHit):
            query = entry.query
            resource = entry.resource
        else:
            query = str(entry.get("query") or "")
            resource = str(entry.get("resource") or "all")
        inp = self.query_one("#usearch-input", Input)
        inp.value = query
        allowed = {kid for kid, _ in self._kind_tabs}
        if resource in allowed:
            self.resource = resource
            self._refresh_kind_strip()
        self._search(query)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Let the input own printable keys and Enter (search) while typing.
        # Up/down still recall project search history while the input is focused.
        if self._input_focused():
            if action in {
                "kind_1",
                "kind_2",
                "kind_3",
                "kind_4",
                "kind_5",
                "collect",
                "inspect",
            }:
                return False
            if action in {"cursor_down", "cursor_up"} and not self._history:
                return False
        return True

    def _refresh_kind_strip(self) -> None:
        for kind_id, _label in self._kind_tabs:
            try:
                label = self.query_one(f"#usearch-kind-{kind_id}", Label)
            except Exception:  # noqa: BLE001
                continue
            label.set_class(kind_id == self.resource, "-active")

    def _set_kind(self, kind: str) -> None:
        allowed = {kid for kid, _ in self._kind_tabs}
        if kind not in allowed:
            return
        if kind == self.resource:
            return
        self.resource = kind
        self._refresh_kind_strip()
        # Re-run only if the user already searched once (or scoped list modes).
        if self._searched or kind in {
            "metrics_over_entities",
            "entities_over_metrics",
        }:
            value = self.query_one("#usearch-input", Input).value.strip()
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
        scoped = self.resource in {
            "metrics_over_entities",
            "entities_over_metrics",
        }
        if not query and not scoped:
            self.query_one("#usearch-status", Static).update(
                "Type a query, then press Enter."
            )
            return
        self._search(query)

    @on(ListView.Selected, "#usearch-list")
    def _result_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, _HistoryHit):
            self._apply_history_entry(item)
            return
        if isinstance(item, _Hit):
            self._dismiss_inspect(item)

    def action_inspect(self) -> None:
        # Input owns Enter → search (see check_action + Input.Submitted).
        if self._input_focused():
            return
        listing = self.query_one("#usearch-list", ListView)
        item = listing.highlighted_child
        if isinstance(item, _HistoryHit):
            self._apply_history_entry(item)
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

    def _row_label(self, row: Any) -> tuple[str, str]:
        if isinstance(row, dict):
            rid = str(row.get("id") or "")
            label_text = str(
                row.get("label") or row.get("name") or rid
            )
        else:
            rid = str(getattr(row, "id", ""))
            label_text = str(
                getattr(row, "label", None)
                or getattr(row, "name", None)
                or rid
            )
        return rid, label_text

    @work(exclusive=True, group="usearch")
    async def _search(self, query: str) -> None:
        self._searched = True
        self._showing_history = False
        self._history_cursor = -1
        status = self.query_one("#usearch-status", Static)
        label = next(
            (lab for kid, lab in self._kind_tabs if kid == self.resource),
            "everything",
        )
        qbit = f" for “{query}”" if query else ""
        status.update(f"Searching {label}{qbit}…")
        if query.strip():
            self._history = remember_search(
                self.project_id,
                query=query,
                resource=self.resource,
                app_dir=self.app_dir,
            )

        hits: list[_Hit] = []
        try:
            if self.resource == "metrics_over_entities":
                if not self.selected_entities:
                    status.update("Open query has no entities to scope metrics")
                    self._set_hits([])
                    return
                result = disc.search_metrics(
                    self.client,
                    query or None,
                    entity=self.selected_entities,
                    mode="union",
                    limit=40,
                    project=self.project_id,
                    investigation=self.investigation_id,
                    app_dir=self.app_dir,
                )
                rows = result.data if isinstance(result.data, list) else []
                for row in rows[:30]:
                    rid, label_text = self._row_label(row)
                    if rid:
                        hits.append(_Hit("metric", rid, label_text))
            elif self.resource == "entities_over_metrics":
                if not self.selected_metrics:
                    status.update("Open query has no metrics to scope entities")
                    self._set_hits([])
                    return
                result = disc.search_entities(
                    self.client,
                    query or None,
                    metric=self.selected_metrics,
                    mode="union",
                    limit=40,
                    project=self.project_id,
                    investigation=self.investigation_id,
                    app_dir=self.app_dir,
                )
                rows = result.data if isinstance(result.data, list) else []
                for row in rows[:30]:
                    rid, label_text = self._row_label(row)
                    if rid:
                        hits.append(_Hit("entity", rid, label_text))
            else:
                if len(query) < 1:
                    self._set_hits([])
                    status.update("")
                    return
                kinds = (
                    ["entity", "metric"]
                    if self.resource == "all"
                    else [self.resource]
                )
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
                        rid, label_text = self._row_label(row)
                        if rid:
                            hits.append(_Hit(kind, rid, label_text))
        except Exception as exc:  # noqa: BLE001
            status.update(f"Search failed: {exc}")
            self._set_hits([])
            return

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
        return {
            "kind": "search",
            "query": q,
            "resource": self.resource,
            "searched": self._searched,
            "selected_entities": list(self.selected_entities),
            "selected_metrics": list(self.selected_metrics),
            "hit_count": len(self._hits),
            "hits": [
                {"type": h.kind, "id": h.resource_id, "label": h.label}
                for h in self._hits[:40]
            ],
        }

    def bug_report_summary(self) -> str:
        try:
            q = self.query_one("#usearch-input", Input).value.strip()
        except Exception:  # noqa: BLE001
            q = ""
        return (
            f"[dim]Search[/] [bold]{self.resource}[/] "
            f"[dim]“{q or '…'}”[/]"
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
        if self._input_focused() and self._history:
            # Toward newer / clear (shell-style history).
            self._history_step(-1)
            return
        self.query_one("#usearch-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self._input_focused() and self._history:
            # Toward older entries.
            self._history_step(1)
            return
        self.query_one("#usearch-list", ListView).action_cursor_up()

    def _history_step(self, delta: int) -> None:
        if not self._history:
            return
        n = len(self._history)
        if self._history_cursor < 0 and delta > 0:
            nxt = 0
        else:
            nxt = self._history_cursor + delta
        if nxt < -1:
            nxt = -1
        if nxt >= n:
            nxt = n - 1
        self._history_cursor = nxt
        inp = self.query_one("#usearch-input", Input)
        if nxt < 0:
            inp.value = ""
            return
        entry = self._history[nxt]
        inp.value = entry.get("query") or ""
        resource = entry.get("resource") or "all"
        allowed = {kid for kid, _ in self._kind_tabs}
        if resource in allowed and resource != self.resource:
            self.resource = resource
            self._refresh_kind_strip()

    def action_cycle_kind(self) -> None:
        cycle = [kid for kid, _ in self._kind_tabs]
        try:
            i = cycle.index(self.resource)
        except ValueError:
            i = 0
        self._set_kind(cycle[(i + 1) % len(cycle)])

    def _kind_by_index(self, index: int) -> None:
        if 1 <= index <= len(self._kind_tabs):
            self._set_kind(self._kind_tabs[index - 1][0])

    def action_kind_1(self) -> None:
        self._kind_by_index(1)

    def action_kind_2(self) -> None:
        self._kind_by_index(2)

    def action_kind_3(self) -> None:
        self._kind_by_index(3)

    def action_kind_4(self) -> None:
        self._kind_by_index(4)

    def action_kind_5(self) -> None:
        self._kind_by_index(5)
