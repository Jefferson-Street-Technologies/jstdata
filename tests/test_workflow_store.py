"""Tests for saved workflows store and CLI."""

from pathlib import Path

import pytest
from click.testing import CliRunner

import jstdata.workflows  # noqa: F401 — register steps
from jstdata.cli import cli
from jstdata.workflows.base import PipelineError
from jstdata.workflows.store import (
    SavedWorkflow,
    WorkflowStoreError,
    create_workflow_from_tokens,
    delete_workflow,
    format_pipeline,
    list_saved_workflows,
    load_workflow,
    resolve_saved_workflow,
    save_workflow,
    validate_workflow_id,
    workflow_exists,
    workflow_to_tokens,
)


def test_validate_workflow_id_slug():
    assert validate_workflow_id("gdp-rank") == "gdp-rank"
    assert validate_workflow_id("a") == "a"
    with pytest.raises(WorkflowStoreError, match="slug"):
        validate_workflow_id("GDP Rank")
    with pytest.raises(WorkflowStoreError, match="slug"):
        validate_workflow_id("gdp_rank")
    with pytest.raises(WorkflowStoreError, match="slug"):
        validate_workflow_id("-gdp")


def test_create_save_load_round_trip(tmp_path: Path):
    wf = create_workflow_from_tokens(
        "gdp-rank",
        "Countries by GDP",
        ["console", "--taxonomy", "country", ":", "rank", "--taxonomy", "country"],
    )
    path = save_workflow(wf, root=tmp_path)
    assert path.name == "gdp-rank.yaml"
    assert workflow_exists("gdp-rank", root=tmp_path)

    loaded = load_workflow("gdp-rank", root=tmp_path)
    assert loaded.id == "gdp-rank"
    assert loaded.description == "Countries by GDP"
    assert [s.id for s in loaded.steps] == ["console", "rank"]
    assert loaded.steps[0].args == {"taxonomy": "country"}
    assert loaded.steps[1].args == {
        "taxonomy": "country",
        "session_entities": True,
    }

    tokens = workflow_to_tokens(loaded)
    assert tokens == [
        "console",
        "--taxonomy",
        "country",
        ":",
        "rank",
        "--taxonomy",
        "country",
        "--session-entities",
    ]
    assert (
        "console --taxonomy country : rank --taxonomy country --session-entities"
        == format_pipeline(loaded)
    )

    resolved = resolve_saved_workflow(loaded)
    assert [r.spec.id for r in resolved] == ["console", "rank"]
    assert resolved[0].kwargs["taxonomy"] == "country"


def test_resolve_saved_workflow_rejects_unknown_step(tmp_path: Path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "id: bad\ndescription: ''\nsteps:\n  - id: not-a-real-step\n",
        encoding="utf-8",
    )
    loaded = load_workflow("bad", root=tmp_path)
    with pytest.raises((PipelineError, KeyError)):
        resolve_saved_workflow(loaded)


def test_list_and_delete(tmp_path: Path):
    wf = create_workflow_from_tokens("solo", "", ["console"])
    save_workflow(wf, root=tmp_path)
    assert [w.id for w in list_saved_workflows(root=tmp_path)] == ["solo"]
    delete_workflow("solo", root=tmp_path)
    assert list_saved_workflows(root=tmp_path) == []
    with pytest.raises(WorkflowStoreError, match="No workflow"):
        delete_workflow("solo", root=tmp_path)


def test_cli_workflow_create_requires_double_dash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "sys.argv",
        ["jst", "workflow", "create", "--id", "x", "console"],
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["workflow", "create", "--id", "x", "console"],
    )
    assert result.exit_code == 2
    assert "after '--'" in result.output


def test_cli_workflows_alias_still_works(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "sys.argv",
        ["jst", "workflows", "create", "--id", "x", "console"],
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["workflows", "create", "--id", "x", "console"],
    )
    assert result.exit_code == 2
    assert "after '--'" in result.output


def test_cli_workflow_create_ls_rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "jstdata.workflows.store.WORKFLOWS_DIR", tmp_path
    )
    runner = CliRunner()

    created = runner.invoke(
        cli,
        [
            "workflow",
            "create",
            "--id",
            "gdp-rank",
            "--description",
            "GDP board",
            "--",
            "console",
            "--taxonomy",
            "country",
            ":",
            "rank",
            "--taxonomy",
            "country",
        ],
    )
    assert created.exit_code == 0, created.output
    assert "gdp-rank" in created.output
    assert (tmp_path / "gdp-rank.yaml").is_file()

    listed = runner.invoke(cli, ["workflow", "ls"])
    assert listed.exit_code == 0
    assert "gdp-rank" in listed.output
    assert "GDP board" in listed.output
    assert "console --taxonomy country" in listed.output

    removed = runner.invoke(cli, ["workflow", "rm", "gdp-rank"])
    assert removed.exit_code == 0
    assert not (tmp_path / "gdp-rank.yaml").exists()


def test_cli_workflow_run_errors_without_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("jstdata.workflows.store.WORKFLOWS_DIR", tmp_path)
    (tmp_path / "stale.yaml").write_text(
        "id: stale\ndescription: ''\nsteps:\n  - id: does-not-exist\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["workflow", "run", "stale"])
    assert result.exit_code != 0
    assert "Error" in result.output or "Unknown" in result.output


def test_cli_workflow_create_overwrite_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("jstdata.workflows.store.WORKFLOWS_DIR", tmp_path)
    runner = CliRunner()
    args = [
        "workflow",
        "create",
        "--id",
        "dup",
        "--",
        "console",
    ]
    first = runner.invoke(cli, args)
    assert first.exit_code == 0, first.output

    abort = runner.invoke(cli, args, input="n\n")
    assert abort.exit_code == 0
    assert "Aborted" in abort.output

    again = runner.invoke(
        cli,
        ["workflow", "create", "--id", "dup", "--description", "v2", "--", "discover"],
        input="y\n",
    )
    assert again.exit_code == 0, again.output
    loaded = load_workflow("dup", root=tmp_path)
    assert loaded.description == "v2"
    assert [s.id for s in loaded.steps] == ["discover"]
