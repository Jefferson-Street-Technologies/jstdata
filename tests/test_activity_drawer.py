"""Smoke tests for the Research activity (Shift+L) drawer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from textual.app import App
from textual.widgets import Input, Static

from jstdata.investigation.activity_drawer import ActivityDrawer, _EventCard
from jstdata.ops.logstore import ActionLogStore


def _seed_store(tmp_path: Path) -> ActionLogStore:
    store = ActionLogStore(app_dir=tmp_path)
    art = store.write_artifact(
        operation="search.entities",
        inputs={"query": "homebuilders"},
        data=[{"id": "cik:1", "label": "Lennar"}],
    )
    store.record_event(
        event_id="evt_drawer_1",
        operation="search.entities",
        status="success",
        inputs={"query": "homebuilders"},
        started_at="2026-11-26T10:42:00+00:00",
        completed_at="2026-11-26T10:42:01+00:00",
        duration_ms=10,
        artifact_id=art,
        actor="agent",
        project_id="demo",
        investigation_id="demo",
    )
    art_q = store.write_artifact(
        operation="query.run",
        inputs={"metric": ["revenues"], "entity": ["cik:1"]},
        data={
            "query": {
                "metric": ["revenues"],
                "entity": ["cik:1"],
                "frequency": "quarterly",
            },
            "record_count": 1,
            "records": [
                {
                    "id": "s1",
                    "label": "Revenues of Lennar",
                    "frequency": "quarterly",
                    "units": "usd",
                    "source": "edgar",
                    "entities": [{"id": "cik:1", "label": "Lennar"}],
                    "observations": [
                        {
                            "observation_timestamp": "2025-07-31T00:00:00+00:00",
                            "value": 8_800_000_000,
                        }
                    ],
                }
            ],
        },
    )
    store.record_event(
        event_id="evt_drawer_2",
        operation="query.run",
        status="success",
        inputs={"metric": ["revenues"], "entity": ["cik:1"]},
        started_at="2026-11-26T10:43:00+00:00",
        completed_at="2026-11-26T10:43:05+00:00",
        duration_ms=50,
        artifact_id=art_q,
        actor="tui",
        project_id="demo",
        investigation_id="demo",
    )
    return store


def test_activity_drawer_renders_research_trail(tmp_path: Path):
    store = _seed_store(tmp_path)

    class Pilot(App):
        def __init__(self) -> None:
            super().__init__()
            self.ok = False
            self.title_text = ""
            self.card_titles: list[str] = []

        def on_mount(self) -> None:
            self.push_screen(
                ActivityDrawer(
                    project_id="demo",
                    investigation_id="demo",
                    app_dir=tmp_path,
                )
            )
            self.call_later(self._drive)

        async def _drive(self) -> None:
            await asyncio.sleep(0.08)
            screen = self.screen
            assert isinstance(screen, ActivityDrawer)
            assert not isinstance(self.focused, Input)
            title = screen.query_one("#activity-title", Static)
            self.title_text = str(title.render())
            assert "Research activity" in self.title_text
            sub = str(screen.query_one("#activity-subtitle", Static).render())
            assert "2 actions" in sub
            assert "1 data request" in sub
            cards = [c for c in screen.query(_EventCard)]
            assert len(cards) >= 2
            self.card_titles = [c.presented.title for c in cards]
            assert "Searched entities" in self.card_titles
            assert "Retrieved data" in self.card_titles
            # Expand the retrieval card.
            retrieve = next(
                c for c in cards if c.presented.operation == "query.run"
            )
            listing = screen.query_one("#activity-list")
            for i, child in enumerate(listing.children):
                if child is retrieve:
                    listing.index = i
                    break
            await screen.run_action("toggle_expand")
            await asyncio.sleep(0.05)
            retrieve = next(
                c
                for c in screen.query(_EventCard)
                if c.event_id == "evt_drawer_2"
            )
            assert retrieve.expanded is True
            assert retrieve.presented.preview_rows
            self.ok = True
            self.exit()

    with patch(
        "jstdata.investigation.activity_drawer.get_store",
        return_value=store,
    ):
        app = Pilot()
        app.run()
    assert app.ok
    assert "Research activity" in app.title_text
