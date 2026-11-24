"""Incremental query editor (e) — draft buffer with Update / Save as new."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, Static

from .api import add_query, persist_investigation
from .model import Investigation, Query


class QueryEditModal(ModalScreen[Optional[dict[str, Any]]]):
    """Edit metric/entity lists and filters for an existing query."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "update", "Update", show=True),
        Binding("ctrl+n", "save_as_new", "Save as new", show=True),
        Binding("slash", "add_entity", "Add entity", show=False),
        Binding("m", "add_metric", "Add metric", show=False),
    ]

    CSS = """
    QueryEditModal { align: center middle; }
    #qe-panel {
        width: 80%;
        height: auto;
        max-height: 80%;
        border: solid #4ade80;
        background: #0c0c0c;
        padding: 1;
    }
    #qe-title { text-style: bold; height: 1; margin-bottom: 1; }
    .qe-row { height: auto; margin-bottom: 1; }
    .qe-label { width: 12; color: #888; }
    #qe-actions { height: 3; align: left middle; }
    """

    def __init__(
        self,
        query: Query,
        *,
        investigation: Investigation,
        app_dir: Optional[Path] = None,
        project_id: Optional[str] = None,
        investigation_id: Optional[str] = None,
    ) -> None:
        super().__init__()
        # Do not name this ``query`` — that shadows Textual's DOM ``query()``.
        self.edit_query = query
        self.investigation = investigation
        self.app_dir = app_dir
        self.project_id = project_id
        self.investigation_id = investigation_id
        # Working copy
        self._metric = list(query.metric)
        self._entity = list(query.entity)
        self._series = list(query.series)
        self._tail = query.tail
        self._head = query.head
        self._frequency = query.frequency
        self._name = query.name or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="qe-panel"):
            yield Static(f"Edit query {self.edit_query.id}", id="qe-title")
            yield from self._row("Name", "qe-name", self._name or "")
            yield Static("", id="qe-entities")
            yield Static("", id="qe-metrics")
            yield Static("", id="qe-series")
            yield from self._row("Frequency", "qe-freq", self._frequency or "")
            yield from self._row(
                "Tail", "qe-tail", "" if self._tail is None else str(self._tail)
            )
            yield from self._row(
                "Head", "qe-head", "" if self._head is None else str(self._head)
            )
            with Horizontal(id="qe-actions"):
                yield Button("Update", id="qe-update", variant="success")
                yield Button("Save as new", id="qe-save-new")
                yield Button("Cancel", id="qe-cancel")
            yield Footer()

    def _row(self, label: str, input_id: str, value: str) -> ComposeResult:
        with Horizontal(classes="qe-row"):
            yield Label(label, classes="qe-label")
            yield Input(value=value, id=input_id)

    def on_mount(self) -> None:
        self._refresh_chips()
        self.query_one("#qe-name", Input).focus()

    def _refresh_chips(self) -> None:
        self.query_one("#qe-entities", Static).update(
            "Entities  " + ("  ".join(f"[{e}]" for e in self._entity) or "[+]")
        )
        self.query_one("#qe-metrics", Static).update(
            "Metrics   " + ("  ".join(f"[{m}]" for m in self._metric) or "[+]")
        )
        self.query_one("#qe-series", Static).update(
            "Series    " + ("  ".join(f"[{s}]" for s in self._series) or "[+]")
        )

    def _read_fields(self) -> None:
        self._name = self.query_one("#qe-name", Input).value.strip()
        freq = self.query_one("#qe-freq", Input).value.strip()
        self._frequency = freq or None
        tail_raw = self.query_one("#qe-tail", Input).value.strip()
        head_raw = self.query_one("#qe-head", Input).value.strip()
        self._tail = int(tail_raw) if tail_raw.isdigit() else None
        self._head = int(head_raw) if head_raw.isdigit() else None

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_update(self) -> None:
        self._apply(save_as_new=False)

    def action_save_as_new(self) -> None:
        self._apply(save_as_new=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "qe-cancel":
            self.dismiss(None)
        elif event.button.id == "qe-update":
            self._apply(save_as_new=False)
        elif event.button.id == "qe-save-new":
            self._apply(save_as_new=True)

    def _apply(self, *, save_as_new: bool) -> None:
        self._read_fields()
        if save_as_new:
            q = add_query(
                self.investigation,
                name=self._name or f"Query {self.edit_query.id} copy",
                metric=list(self._metric),
                entity=list(self._entity),
                series=list(self._series),
                frequency=self._frequency,
                head=self._head,
                tail=self._tail,
                app_dir=self.app_dir,
                persist=False,
            )
            q.in_report = True
            persist_investigation(self.investigation, app_dir=self.app_dir)
            self.dismiss({"query_id": q.id, "mode": "new"})
            return

        q = self.edit_query
        q.name = self._name or q.name
        q.metric = list(self._metric)
        q.entity = list(self._entity)
        q.series = list(self._series)
        q.frequency = self._frequency
        q.head = self._head
        q.tail = self._tail
        persist_investigation(self.investigation, app_dir=self.app_dir)
        self.dismiss({"query_id": q.id, "mode": "update"})

    def action_add_entity(self) -> None:
        self._open_picker("entity")

    def action_add_metric(self) -> None:
        self._open_picker("metric")

    def _open_picker(self, kind: str) -> None:
        from .universal_search import UniversalSearchModal

        app = self.app
        client = getattr(app, "client", None)
        if client is None:
            return

        def _done(result: object | None) -> None:
            if not isinstance(result, dict):
                return
            rid = str(result.get("id") or "")
            if not rid:
                return
            if kind == "entity" and rid not in self._entity:
                self._entity.append(rid)
            elif kind == "metric" and rid not in self._metric:
                self._metric.append(rid)
            self._refresh_chips()

        self.app.push_screen(
            UniversalSearchModal(
                client,
                context="add_to_query",
                resource=kind,
                app_dir=self.app_dir,
                project_id=self.project_id,
                investigation_id=self.investigation_id,
            ),
            _done,
        )
