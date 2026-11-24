"""SQLite action log + artifact cache."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.models import Entity
from jstdata.ops.discovery import search_entities
from jstdata.ops.logstore import ActionLogStore
from jstdata.ops.runner import run_operation


def test_store_round_trip(tmp_path: Path):
    store = ActionLogStore(app_dir=tmp_path)
    art = store.write_artifact(
        operation="search.entities",
        inputs={"query": "apple"},
        data=[{"id": "cik:1", "label": "Apple"}],
    )
    store.record_event(
        event_id="evt_test1",
        operation="search.entities",
        status="success",
        inputs={"query": "apple"},
        started_at="2024-01-01T00:00:00+00:00",
        completed_at="2024-01-01T00:00:01+00:00",
        duration_ms=12,
        artifact_id=art,
        project_id="demo",
        investigation_id="demo",
    )
    events = store.list_events(project_id="demo")
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_test1"
    loaded = store.get_event("evt_test1")
    assert loaded["artifact"]["data"][0]["id"] == "cik:1"


def test_run_operation_persists_and_caches(tmp_path: Path):
    store = ActionLogStore(app_dir=tmp_path)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"id": "united-states", "label": "United States"}]

    first = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "united"},
        store=store,
    )
    assert first.status == "success"
    assert first.cache_hit is False
    assert first.artifact_id
    assert calls["n"] == 1

    second = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "united"},
        store=store,
    )
    assert second.status == "success"
    assert second.cache_hit is True
    assert second.artifact_id == first.artifact_id
    assert calls["n"] == 1  # fn not re-run
    assert second.data[0]["id"] == "united-states"

    events = store.list_events()
    assert len(events) == 2
    assert events[0]["cache_hit"] is True
    assert events[1]["cache_hit"] is False


def test_run_operation_refresh_bypasses_cache(tmp_path: Path):
    store = ActionLogStore(app_dir=tmp_path)
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"id": f"id-{calls['n']}", "label": f"v{calls['n']}"}]

    first = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "x"},
        store=store,
    )
    assert first.cache_hit is False
    assert calls["n"] == 1

    cached = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "x"},
        store=store,
    )
    assert cached.cache_hit is True
    assert calls["n"] == 1

    refreshed = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "x"},
        store=store,
        use_cache=False,
    )
    assert refreshed.cache_hit is False
    assert calls["n"] == 2
    assert refreshed.data[0]["id"] == "id-2"
    # Fresh artifact becomes the cache head for later hits.
    assert refreshed.artifact_id != first.artifact_id

    after = run_operation(
        "search.entities",
        fetch,
        inputs={"query": "x"},
        store=store,
    )
    assert after.cache_hit is True
    assert after.artifact_id == refreshed.artifact_id
    assert after.data[0]["id"] == "id-2"
    assert calls["n"] == 2


def test_failed_ops_are_logged(tmp_path: Path):
    store = ActionLogStore(app_dir=tmp_path)

    def boom():
        raise ValueError("nope")

    result = run_operation("search.entities", boom, inputs={}, store=store)
    assert result.status == "error"
    assert result.error == "nope"
    events = store.list_events()
    assert events[0]["status"] == "error"
    assert events[0]["artifact_id"] is None


def test_cli_log_and_inspect(tmp_path: Path, monkeypatch):
    import jstdata.client as client_mod
    import jstdata.cli_actions as cli_actions
    import jstdata.investigation.active as active_mod

    monkeypatch.setattr(client_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)

    fake = MagicMock()
    fake.search_entities.return_value = [
        Entity.from_dict({"id": "united-states", "label": "United States"})
    ]
    monkeypatch.setattr(cli_actions, "_client", lambda: fake)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["actions", "search", "entities", "united", "--limit", "3"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    event_id = payload["event_id"]
    assert payload["artifact_id"]

    # Second call should be a cache hit (same inputs).
    result2 = runner.invoke(
        cli, ["actions", "search", "entities", "united", "--limit", "3"]
    )
    assert result2.exit_code == 0, result2.output
    payload2 = json.loads(result2.output)
    assert payload2["cache_hit"] is True
    assert payload2["artifact_id"] == payload["artifact_id"]
    assert fake.search_entities.call_count == 1

    log = runner.invoke(cli, ["actions", "log", "--limit", "10"])
    assert log.exit_code == 0, log.output
    log_payload = json.loads(log.output)
    assert log_payload["data"]["count"] >= 2

    inspect = runner.invoke(cli, ["actions", "inspect", event_id])
    assert inspect.exit_code == 0, inspect.output
    inspected = json.loads(inspect.output)
    assert inspected["data"]["event_id"] == event_id
    assert inspected["data"]["artifact"]["data"][0]["id"] == "united-states"
