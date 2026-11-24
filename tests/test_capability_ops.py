"""Logged query.run, workspace mutate, annotate, rerun, promote-from-run."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.investigation.api import create_investigation
from jstdata.investigation.project_store import load_project_investigation
from jstdata.models import Observation, Series, TimeSeries
from jstdata.ops import query_ops, workspace_ops
from jstdata.ops.curation import promote_event
from jstdata.ops.logstore import ActionLogStore
from jstdata.ops.rerun import rerun_event
from datetime import datetime, timezone


def _ts(series_id: str = "s1") -> TimeSeries:
    series = Series(
        id=series_id,
        label="S",
        frequency="quarterly",
        source="test",
        units="USD",
        seasonal_adjustment="",
        last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metric_id="revenue",
        entities=[],
    )
    obs = Observation(
        series_id=series_id,
        observation_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        release_timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
        value=1.5,
        release_id="r1",
    )
    return TimeSeries(series=series, observations=[obs])


def _project(tmp_path: Path):
    inv = create_investigation(
        "fundamentals",
        title="Fundamentals",
        app_dir=tmp_path,
    )
    # create_investigation returns inv; project id is on disk
    from jstdata.investigation.active import try_load_active

    ctx = try_load_active(app_dir=tmp_path)
    assert ctx is not None
    return ctx.project, inv.id


def test_query_run_logs_artifact_and_promote(tmp_path: Path, monkeypatch):
    project_id, inv_id = _project(tmp_path)
    client = MagicMock()
    client.query.return_value = [_ts()]

    monkeypatch.setenv("HOME", str(tmp_path))  # unused; we pass app_dir
    store = ActionLogStore(app_dir=tmp_path)

    result = query_ops.run_query_op(
        client,
        metric=["revenue"],
        entity=["cik:1"],
        tail=1,
        save=True,
        name="Revenue",
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
        use_cache=False,
    )
    assert result.status == "success"
    assert result.artifact_id
    assert result.data["saved_query_id"] == 1
    assert result.data["query"]["in_report"] is False
    assert result.data["record_count"] == 1
    client.query.assert_called_once()

    inv = load_project_investigation(
        project_id, inv_id, app_dir=tmp_path
    )
    assert inv.queries[1].in_report is False

    promoted = promote_event(
        result.event_id,
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
        store=store,
    )
    assert promoted.status == "success"
    inv2 = load_project_investigation(
        project_id, inv_id, app_dir=tmp_path
    )
    # promote from event adds another report query (or marks)
    report = [q for q in inv2.queries.values() if q.in_report]
    assert report


def test_workspace_add_rm_and_annotate(tmp_path: Path):
    project_id, inv_id = _project(tmp_path)
    added = workspace_ops.workspace_add(
        metric=["revenue"],
        entity=["cik:1"],
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
    )
    assert added.status == "success"
    assert "revenue" in added.data["workspace"]["metric"]

    note = workspace_ops.annotate_op(
        "Watch margins",
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
    )
    assert note.status == "success"
    inv = load_project_investigation(
        project_id, inv_id, app_dir=tmp_path
    )
    assert inv.annotations == "Watch margins"

    removed = workspace_ops.workspace_rm(
        ["revenue"],
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
    )
    assert removed.status == "success"
    assert "revenue" not in removed.data["workspace"]["metric"]


def test_rerun_search(tmp_path: Path):
    project_id, inv_id = _project(tmp_path)
    store = ActionLogStore(app_dir=tmp_path)
    client = MagicMock()
    from jstdata.models import Entity
    from jstdata.ops.discovery import search_entities

    client.search_entities.return_value = [
        Entity(id="cik:1", label="Co")
    ]
    first = search_entities(
        client,
        "apple",
        project=project_id,
        investigation=inv_id,
        app_dir=tmp_path,
        use_cache=False,
    )
    assert first.status == "success"
    client.search_entities.return_value = [
        Entity(id="cik:2", label="Other")
    ]
    second = rerun_event(
        first.event_id,
        refresh=True,
        client=client,
        app_dir=tmp_path,
    )
    assert second.status == "success"
    assert second.event_id != first.event_id
    assert second.data[0]["id"] == "cik:2"
    assert len(store.list_events()) >= 2


def test_concurrent_log_writes(tmp_path: Path):
    """Smoke: concurrent action-log writers stay consistent under WAL."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from jstdata.ops.runner import run_operation

    store = ActionLogStore(app_dir=tmp_path)

    def write(i: int):
        return run_operation(
            "search.entities",
            lambda: [{"id": f"e{i}", "label": str(i)}],
            inputs={"query": f"q{i}", "limit": 1, "offset": 0},
            store=store,
            use_cache=False,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write, i) for i in range(20)]
        results = [f.result() for f in as_completed(futures)]
    assert all(r.status == "success" for r in results)
    assert len({r.event_id for r in results}) == 20
    assert len(store.list_events(limit=100)) == 20


def test_query_run_cli(tmp_path: Path, monkeypatch):
    project_id, inv_id = _project(tmp_path)

    def fake_run(client, **kwargs):
        from jstdata.ops.result import OperationResult

        return OperationResult(
            operation="query.run",
            status="success",
            event_id="evt_cli",
            data={"records": [], "record_count": 0, "query": {}, "saved_query_id": None},
            project_id=kwargs.get("project"),
            investigation_id=kwargs.get("investigation"),
        )

    monkeypatch.setattr("jstdata.ops.query_ops.run_query_op", fake_run)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "actions",
            "query",
            "run",
            "--metric",
            "revenue",
            "--entity",
            "cik:1",
            "--tail",
            "1",
            "--project",
            project_id,
            "--investigation",
            inv_id,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "evt_cli" in result.output
