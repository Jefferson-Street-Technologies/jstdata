"""Slice 6: feedback outbox, promote, copy, publication projection."""

from __future__ import annotations

from pathlib import Path

from jstdata.investigation import (
    add_investigation_to_project,
    create_project_with_investigation,
    load_project_investigation,
    persist_investigation,
    set_active,
)
from jstdata.investigation.api import add_query
from jstdata.ops.curation import (
    copy_across_investigations,
    promote_event,
    promote_query,
    publication_projection,
)
from jstdata.ops.feedback import (
    inspect_feedback,
    list_feedback,
    submit_feedback,
)
from jstdata.ops.logstore import ActionLogStore
from jstdata.ops.runner import run_operation


def test_feedback_outbox_redacts_secrets(tmp_path: Path):
    store = ActionLogStore(app_dir=tmp_path)
    result = submit_feedback(
        "Charts look wrong on Michigan",
        project_id="ftai",
        investigation_id="peers",
        screen="EntityScreen",
        diagnostics={
            "api_key": "sk-secret",
            "Authorization": "Bearer x",
            "ok": True,
        },
        store=store,
        app_dir=tmp_path,
    )
    assert result.status == "success"
    fid = result.data["feedback_id"]
    assert result.data["diagnostics"]["api_key"] == "[redacted]"
    assert result.data["diagnostics"]["Authorization"] == "[redacted]"
    assert result.data["diagnostics"]["ok"] is True

    listed = list_feedback(store=store, app_dir=tmp_path)
    assert listed.data["count"] == 1
    inspected = inspect_feedback(fid, store=store, app_dir=tmp_path)
    assert inspected.data["message"].startswith("Charts")


def test_publication_projection_drops_workspace_and_exploratory(tmp_path: Path):
    _, inv = create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    inv.workspace.add_metric("gdp")
    inv.workspace.add_entity("united-states")
    report = add_query(
        inv,
        name="GDP",
        metric=["gdp"],
        entity=["united-states"],
        app_dir=tmp_path,
        persist=False,
    )
    report.in_report = True
    explor = add_query(
        inv,
        name="Scratch",
        metric=["cpi"],
        entity=["united-states"],
        app_dir=tmp_path,
        persist=False,
    )
    explor.in_report = False
    persist_investigation(inv, app_dir=tmp_path)

    projected = publication_projection(inv)
    assert "workspace" not in projected
    assert len(projected["queries"]) == 1
    assert projected["queries"][0]["id"] == report.id
    assert projected["queries"][0]["in_report"] is True


def test_promote_query_and_copy_across(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai", investigation_id="peers", title="Peers", app_dir=tmp_path
    )
    set_active("fundamentals", project="ftai", app_dir=tmp_path)
    fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    q = add_query(
        fund,
        name="Debt",
        metric=["long-term-debt"],
        entity=["cik:320193"],
        app_dir=tmp_path,
        persist=False,
    )
    q.in_report = False
    persist_investigation(fund, app_dir=tmp_path)

    promoted = promote_query(
        q.id, project="ftai", investigation="fundamentals", app_dir=tmp_path
    )
    assert promoted.status == "success"
    assert promoted.data["in_report"] is True

    fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    assert fund.get_query(q.id).in_report is True
    fund.workspace.add_entity("cik:789019")
    persist_investigation(fund, app_dir=tmp_path)

    copied = copy_across_investigations(
        project_id="ftai",
        source_id="fundamentals",
        dest_id="peers",
        entity=["cik:789019"],
        query_ids=[q.id],
        app_dir=tmp_path,
    )
    assert copied.status == "success"
    peers = load_project_investigation("ftai", "peers", app_dir=tmp_path)
    assert "cik:789019" in peers.workspace.entity
    assert len(peers.queries) == 1
    assert list(peers.queries.values())[0].in_report is True


def test_promote_event_stages_search_entities(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    set_active("fundamentals", project="ftai", app_dir=tmp_path)
    store = ActionLogStore(app_dir=tmp_path)

    def fetch():
        return [
            {"id": "cik:2488", "label": "AMD"},
            {"id": "cik:1045810", "label": "NVDA"},
        ]

    logged = run_operation(
        "search.entities",
        fetch,
        inputs={"relation": ["classified_as:sic:3674"]},
        store=store,
    )
    assert logged.cache_hit is False

    result = promote_event(
        logged.event_id,
        project="ftai",
        investigation="fundamentals",
        app_dir=tmp_path,
        store=store,
    )
    assert result.status == "success", result.error
    assert result.data["mode"] == "workspace"
    fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    assert "cik:2488" in fund.workspace.entity
    assert "cik:1045810" in fund.workspace.entity
