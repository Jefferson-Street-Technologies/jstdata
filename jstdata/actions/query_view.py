"""Query View — run a saved query and inspect series as JSON records.

Idle until ``r``. Results are a list of JSON objects (one per series), not a
pandas table: each record keeps metric/entity/units/source/observations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, ListItem, ListView, Static

from ..client import JSTDataClient
from ..investigation.model import Query, Workspace
from ..models import TimeSeries
from .base import (
    ActionArgument,
    ActionBinding,
    ActionSpec,
    register,
    stage_ids,
)
from .screen import BaseScreen


def series_to_record(ts: TimeSeries) -> dict[str, Any]:
    """JSON-ready record for one ``TimeSeries`` (metadata + observations)."""
    series = ts.series
    observations = [
        {
            "observation_timestamp": obs.observation_timestamp.isoformat(),
            "release_timestamp": obs.release_timestamp.isoformat(),
            "value": obs.value,
            "release_id": obs.release_id,
        }
        for obs in ts.observations
    ]
    return {
        "id": series.id,
        "label": series.label,
        "metric_id": series.metric_id,
        "entities": [{"id": e.id, "label": e.label} for e in series.entities],
        "frequency": series.frequency,
        "units": series.units,
        "source": series.source,
        "seasonal_adjustment": series.seasonal_adjustment,
        "n_observations": len(observations),
        "last": observations[-1] if observations else None,
        "observations": observations,
    }


def _record_line(record: dict[str, Any]) -> str:
    entities = record.get("entities") or []
    entity = entities[0]["id"] if entities else "—"
    last = record.get("last") or {}
    value = last.get("value")
    value_s = "—" if value is None else str(value)
    metric = record.get("metric_id") or "—"
    units = record.get("units") or ""
    return f"{metric}  {entity}  {value_s} {units}".strip()


class SeriesRecordRow(ListItem):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__()
        self.record = record

    def compose(self) -> ComposeResult:
        yield Label(_record_line(self.record))


class QueryScreen(BaseScreen):
    """Saved / scratch query results as a JSON series list."""

    BINDINGS = [
        Binding("r", "load", "Run query", key_display="r"),
        Binding("space", "collect", "Collect into workspace", show=False),
        Binding("j", "cursor_down", "Cursor Down", show=False),
        Binding("k", "cursor_up", "Cursor Up", show=False),
        Binding("enter", "refresh_json", "Show JSON", priority=True),
    ]

    DEFAULT_CSS = """
    #query-body {
        height: 1fr;
    }
    .pane-container {
        border: solid #222;
        background: #0f0f0f;
        height: 1fr;
    }
    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    #query-meta {
        color: #888;
        padding: 0 1;
        height: auto;
        max-height: 3;
    }
    #query-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #query-list {
        height: 1fr;
        background: #0f0f0f;
    }
    #query-json-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    #query-json {
        height: 1fr;
        color: #c0c0c0;
        padding: 0 1;
        background: #0a0a0a;
    }
    ListView > ListItem.--highlight {
        background: #1a3a2a;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        query_id: Optional[int] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self.query_id = int(query_id) if query_id is not None else None
        self._records: list[dict[str, Any]] = []
        self._loaded = False

    def compose_body(self) -> ComposeResult:
        with Vertical(id="query-body"):
            with Vertical(classes="pane-container"):
                yield Label("QUERY", classes="pane-header", id="query-header")
                yield Static("", id="query-meta")
                yield Static(
                    "Idle — press [bold]r[/bold] to run",
                    id="query-status",
                    markup=True,
                )
                yield ListView(id="query-list")
                yield Label("JSON", id="query-json-header")
                yield Static(
                    "Highlight a series, then r to load.",
                    id="query-json",
                )

    def on_mount(self) -> None:
        super().on_mount()
        self._render_idle()
        self.query_one("#query-list", ListView).focus()

    def footer_action_keys(self) -> list[str]:
        keys = ["[bold]r[/bold] run"]
        if self._loaded:
            keys.append("[bold]j[/bold]/[bold]k[/bold] series")
            keys.append("[bold]space[/bold] collect")
        return keys

    def _resolve_query(self) -> Query:
        inv = getattr(self.app, "investigation", None)
        if self.query_id is not None:
            if inv is None:
                raise RuntimeError("No investigation loaded")
            return inv.get_query(self.query_id)
        return Query.from_workspace(self.workspace, query_id=0, name="workspace")

    def _render_idle(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.query_one("#query-header", Label).update("QUERY")
            self.query_one("#query-meta", Static).update(str(exc))
            return
        title = query.name or (f"query {query.id}" if query.id else "workspace")
        self.query_one("#query-header", Label).update(f"QUERY // {title}")
        bits = [
            f"metric={len(query.metric)}",
            f"entity={len(query.entity)}",
            f"series={len(query.series)}",
        ]
        if query.tail is not None:
            bits.append(f"tail={query.tail}")
        if query.head is not None:
            bits.append(f"head={query.head}")
        self.query_one("#query-meta", Static).update(" · ".join(bits) + " · cartesian")

    def action_load(self) -> None:
        try:
            query = self._resolve_query()
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        if query.is_empty():
            self.notify(
                "Query is empty — Search / Collect into the workspace first",
                severity="warning",
            )
            return
        self.load_query(query)

    @work(exclusive=True, group="query-view")
    async def load_query(self, query: Query) -> None:
        status = self.query_one("#query-status", Static)
        status.update("Running /query…")
        try:
            results = await asyncio.to_thread(query.execute, self.client)
        except Exception as exc:
            status.update(f"Query failed: {exc}")
            return
        self._records = [series_to_record(ts) for ts in results]
        self._loaded = True
        list_view = self.query_one("#query-list", ListView)
        list_view.clear()
        for record in self._records:
            list_view.append(SeriesRecordRow(record))
        n = len(self._records)
        status.update(f"{n} series" if n else "No series returned")
        if self._records:
            self._show_json(self._records[0])
        else:
            self.query_one("#query-json", Static).update("{}")
        self.refresh_footer()

    def _highlighted_record(self) -> Optional[dict[str, Any]]:
        child = self.query_one("#query-list", ListView).highlighted_child
        if isinstance(child, SeriesRecordRow):
            return child.record
        return None

    def _show_json(self, record: dict[str, Any]) -> None:
        self.query_one("#query-json", Static).update(
            json.dumps(record, indent=2, default=str)
        )

    def action_refresh_json(self) -> None:
        record = self._highlighted_record()
        if record is None:
            self.notify("No series highlighted", severity="warning")
            return
        self._show_json(record)

    def action_cursor_down(self) -> None:
        lv = self.query_one("#query-list", ListView)
        if hasattr(lv, "action_cursor_down"):
            lv.action_cursor_down()

    def action_cursor_up(self) -> None:
        lv = self.query_one("#query-list", ListView)
        if hasattr(lv, "action_cursor_up"):
            lv.action_cursor_up()

    def action_collect(self) -> None:
        record = self._highlighted_record()
        if record is None:
            self.notify("No series highlighted", severity="warning")
            return
        entities = record.get("entities") or []
        entity_id = entities[0]["id"] if entities else None
        labels = {}
        if entity_id:
            labels[entity_id] = entities[0].get("label") or entity_id
        if record.get("metric_id"):
            labels[record["metric_id"]] = record.get("label") or record["metric_id"]
        if record.get("id"):
            labels[record["id"]] = record.get("label") or record["id"]
        added = stage_ids(
            self.workspace,
            self.app,
            metric=record.get("metric_id"),
            entity=entity_id,
            series=record.get("id"),
            labels=labels,
        )
        if not added:
            self.notify("Already in workspace", severity="warning")
            return
        self.notify("Collected " + ", ".join(added))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "collect" and not self._loaded:
            return False
        return True

    @on(ListView.Highlighted, "#query-list")
    def on_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, SeriesRecordRow):
            self._show_json(event.item.record)


def create_query_screen(client, workspace, query_id=None, **kwargs):
    kwargs.pop("input_context", None)
    kwargs.pop("meta", None)
    return QueryScreen(client, workspace, query_id=query_id)


QUERY_VIEW = register(
    ActionSpec(
        id="query",
        name="Query",
        description=(
            "Run a saved cartesian query and inspect each series as JSON "
            "(metadata + observations). Idle until r; space collects."
        ),
        create_screen=create_query_screen,
        arguments=(
            ActionArgument(
                name="query_id",
                type="integer",
                description="Saved query id (omit to run the workspace bag)",
            ),
        ),
        bindings=(
            ActionBinding("r", "load", "Execute GET /query"),
            ActionBinding("j / k", "cursor", "Move between series"),
            ActionBinding("space", "collect", "Collect metric/entity/series into workspace"),
        ),
        example="jst action add query --args '{\"query_id\":1}'",
    )
)
