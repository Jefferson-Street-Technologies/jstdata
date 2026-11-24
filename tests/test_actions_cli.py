"""Tests for ``jst actions`` and retired CLI groups."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.investigation import create_investigation
from jstdata.ops.result import OperationResult


@pytest.fixture
def runner():
    return CliRunner()


def test_actions_help(runner):
    result = runner.invoke(cli, ["actions"])
    assert result.exit_code == 0
    assert "search" in result.output
    assert "relationships" in result.output


def test_action_singular_retired(runner):
    result = runner.invoke(cli, ["action", "ls"])
    assert result.exit_code == 2
    assert "retired" in result.output.lower()
    assert "jst actions" in result.output


def test_query_group_retired(runner):
    result = runner.invoke(cli, ["query", "ls"])
    assert result.exit_code == 2
    assert "retired" in result.output.lower()


def test_actions_search_entities_json(runner, tmp_path, monkeypatch):
    from jstdata import cli_actions
    from jstdata.models import Entity
    import jstdata.client as client_mod
    import jstdata.investigation.active as active_mod

    monkeypatch.setattr(client_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)

    fake = MagicMock()
    fake.search_entities.return_value = [
        Entity.from_dict({"id": "united-states", "label": "United States"})
    ]
    monkeypatch.setattr(cli_actions, "_client", lambda: fake)

    result = runner.invoke(
        cli,
        ["actions", "search", "entities", "united states", "--limit", "5"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["operation"] == "search.entities"
    assert payload["event_id"].startswith("evt_")
    assert payload["data"][0]["id"] == "united-states"
    assert payload["artifact_id"]
    fake.search_entities.assert_called_once()


def test_actions_relationships_passes_direction(runner, tmp_path, monkeypatch):
    from jstdata import cli_actions
    from jstdata.models import EntityRelationship
    import jstdata.client as client_mod
    import jstdata.investigation.active as active_mod

    monkeypatch.setattr(client_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)

    fake = MagicMock()
    fake.get_entity_relations.return_value = [
        EntityRelationship.from_dict(
            {"id": "cik:1", "relationship": "classified_as", "taxonomy": "sic"}
        )
    ]
    monkeypatch.setattr(cli_actions, "_client", lambda: fake)

    result = runner.invoke(
        cli,
        [
            "actions",
            "relationships",
            "sic:3674",
            "--direction",
            "incoming",
            "--relationship",
            "classified_as",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    call_kwargs = fake.get_entity_relations.call_args.kwargs
    assert call_kwargs.get("direction") == "incoming"
    assert call_kwargs.get("relationship") == "classified_as"


def test_actions_workspace_show(tmp_path: Path, runner, monkeypatch):
    inv = create_investigation("demo", title="Demo", app_dir=tmp_path)
    inv.workspace.add_entity("united-states")
    from jstdata.investigation import persist_investigation

    persist_investigation(inv, app_dir=tmp_path)

    # Point active + project resolution at tmp_path
    import jstdata.investigation.active as active_mod
    import jstdata.client as client_mod

    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr(client_mod, "APP_DIR", tmp_path)

    result = runner.invoke(
        cli,
        [
            "actions",
            "workspace",
            "show",
            "--project",
            "demo",
            "--investigation",
            "demo",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert "united-states" in payload["data"]["entity"]


def test_actions_log_lists_events(runner, tmp_path, monkeypatch):
    import jstdata.client as client_mod
    import jstdata.investigation.active as active_mod

    monkeypatch.setattr(client_mod, "APP_DIR", tmp_path)
    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)
    result = runner.invoke(cli, ["actions", "log"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["operation"] == "actions.log"
    assert "events" in payload["data"]
    assert payload["data"]["count"] == 0
