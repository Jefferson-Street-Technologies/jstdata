"""Human-readable action-log presentation for the Research activity trail."""

from __future__ import annotations

from jstdata.ops.event_present import (
    actor_display,
    actor_filter_label,
    count_trail_stats,
    format_event,
    is_retrieve_op,
)


def test_actor_display_mapping():
    assert actor_display("tui") == "You"
    assert actor_display("cli") == "CLI"
    assert actor_display("agent") == "Agent"
    assert actor_display("agent:peers") == "Agent:peers"
    assert actor_filter_label(None) == "All"
    assert actor_filter_label("tui") == "You"


def test_search_entities_presentation():
    event = {
        "event_id": "evt_1",
        "operation": "search.entities",
        "status": "success",
        "actor": "agent",
        "completed_at": "2026-11-26T10:42:11+00:00",
        "inputs": {"query": "US residential homebuilders"},
        "artifact_id": "art_1",
    }
    hits = [
        {"id": "cik:1", "label": "Lennar"},
        {"id": "cik:2", "label": "D.R. Horton"},
    ]
    presented = format_event(event, artifact_data=hits)
    assert presented.title == "Searched entities"
    assert presented.kind == "search"
    assert presented.actor_label == "Agent"
    assert presented.when == "10:42"
    assert "US residential homebuilders" in presented.secondary_lines
    assert presented.stats_line == "2 results"


def test_metadata_entity_presentation():
    event = {
        "event_id": "evt_2",
        "operation": "metadata.entity",
        "status": "success",
        "actor": "tui",
        "completed_at": "2026-11-26T10:42:40+00:00",
        "inputs": {"entity_id": "cik:920760"},
    }
    presented = format_event(
        event,
        artifact_data={"id": "cik:920760", "label": "Lennar Corporation"},
    )
    assert presented.title == "Explored entity"
    assert presented.kind == "explore"
    assert presented.actor_label == "You"
    assert "Lennar Corporation" in presented.secondary_lines


def test_query_run_preview_rows():
    event = {
        "event_id": "evt_3",
        "operation": "query.run",
        "status": "success",
        "actor": "agent:research",
        "completed_at": "2026-11-26T10:43:00+00:00",
        "inputs": {"metric": ["revenues"], "entity": ["cik:1", "cik:2"]},
        "artifact_id": "art_q",
    }
    artifact = {
        "query": {
            "metric": ["revenues"],
            "entity": ["cik:1", "cik:2"],
            "frequency": "quarterly",
        },
        "record_count": 2,
        "records": [
            {
                "id": "s1",
                "label": "Revenues of Lennar",
                "metric_id": "revenues",
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
            },
            {
                "id": "s2",
                "label": "Revenues of D.R. Horton",
                "metric_id": "revenues",
                "frequency": "quarterly",
                "units": "usd",
                "source": "edgar",
                "entities": [{"id": "cik:2", "label": "D.R. Horton"}],
                "observations": [
                    {
                        "observation_timestamp": "2025-07-31T00:00:00+00:00",
                        "value": 9_400_000_000,
                    }
                ],
            },
        ],
    }
    presented = format_event(event, artifact_data=artifact)
    assert presented.title == "Retrieved data"
    assert presented.kind == "retrieve"
    assert presented.actor_label == "Agent:research"
    assert any("Quarterly" in line or "quarterly" in line.lower() for line in presented.secondary_lines) or any(
        "2025" in line for line in presented.secondary_lines
    )
    assert presented.stats_line is not None
    assert "observation" in presented.stats_line
    assert "SEC EDGAR" in presented.stats_line
    assert len(presented.preview_rows) == 2
    assert presented.preview_rows[0].label == "Lennar"
    assert "Q3" in presented.preview_rows[0].period or "2025" in presented.preview_rows[0].period
    assert presented.preview_rows[0].value.startswith("$")


def test_query_run_economic_title():
    event = {
        "event_id": "evt_eco",
        "operation": "query.run",
        "status": "success",
        "actor": "cli",
        "completed_at": "2026-11-26T10:44:00+00:00",
        "inputs": {"series": ["fred:PERMIT"]},
    }
    artifact = {
        "query": {"series": ["fred:PERMIT"], "frequency": "monthly"},
        "record_count": 1,
        "records": [
            {
                "id": "fred:PERMIT",
                "label": "US building permits",
                "frequency": "monthly",
                "source": "fred",
                "observations": [
                    {
                        "observation_timestamp": "2015-01-01",
                        "value": 100.0,
                    },
                    {
                        "observation_timestamp": "2025-01-01",
                        "value": 140.0,
                    },
                ],
            }
        ],
    }
    presented = format_event(event, artifact_data=artifact)
    assert presented.title == "Retrieved economic data"
    assert presented.actor_label == "CLI"
    assert "US building permits" in presented.secondary_lines[0]


def test_query_patch_expanded():
    event = {
        "event_id": "evt_p",
        "operation": "query.patch",
        "status": "success",
        "actor": "tui",
        "completed_at": "2026-11-26T10:43:30+00:00",
        "inputs": {
            "query_id": 1,
            "add_metric": ["operating-income", "inventory"],
        },
    }
    presented = format_event(event, artifact_data={"before": {}, "query": {}})
    assert presented.title == "Expanded query"
    assert any(line.startswith("+") for line in presented.secondary_lines)


def test_remark_add_presentation():
    event = {
        "event_id": "evt_r",
        "operation": "remark.add",
        "status": "success",
        "actor": "tui",
        "completed_at": "2026-11-26T10:45:00+00:00",
        "inputs": {"query_id": 1, "name": "Homebuilder profitability"},
    }
    artifact = {
        "remark": {
            "name": "Homebuilder profitability",
            "text": "Margin resilience varies.",
            "series": ["s1", "s2"],
        }
    }
    presented = format_event(event, artifact_data=artifact)
    assert presented.title == "Added research remark"
    assert presented.kind == "remark"
    assert "Homebuilder profitability" in presented.secondary_lines
    assert presented.stats_line == "2 supporting citations"


def test_error_presentation():
    event = {
        "event_id": "evt_err",
        "operation": "query.run",
        "status": "error",
        "actor": "agent",
        "completed_at": "2026-11-26T11:00:00+00:00",
        "inputs": {"metric": ["revenues"]},
        "error": "404 not found",
    }
    presented = format_event(event, artifact_data=None)
    assert presented.title == "Data unavailable"
    assert presented.kind == "error"
    assert "404 not found" in presented.secondary_lines


def test_count_trail_stats_and_retrieve_flag():
    events = [
        {"operation": "search.entities"},
        {"operation": "query.run"},
        {"operation": "query.run"},
        {"operation": "remark.add"},
    ]
    assert count_trail_stats(events) == (4, 2)
    assert is_retrieve_op("query.run")
    assert not is_retrieve_op("search.entities")
