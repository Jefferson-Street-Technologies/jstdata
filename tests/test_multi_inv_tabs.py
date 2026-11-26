"""Multi-investigation tab order + lifecycle APIs."""

from __future__ import annotations

from pathlib import Path

import pytest

from jstdata.investigation import (
    InvestigationError,
    add_investigation_to_project,
    clone_project,
    create_project_with_investigation,
    duplicate_investigation,
    load_active,
    load_project,
    load_project_investigation,
    move_investigation,
    persist_investigation,
    remove_investigation_from_project,
    reorder_investigations,
    set_active,
)
from jstdata.investigation.commands import CommandError, parse_command_line
from jstdata.investigation.host import InvestigationHost
from jstdata.investigation.model import ROOT_ACTION_ID
from jstdata.investigation.project_store import project_investigations_dir


def test_cycle_investigation_id_wraps():
    order = ["fundamentals", "peers", "macro"]
    assert (
        InvestigationHost.cycle_investigation_id(order, "fundamentals", 1) == "peers"
    )
    assert InvestigationHost.cycle_investigation_id(order, "macro", 1) == "fundamentals"
    assert InvestigationHost.cycle_investigation_id(order, "peers", -1) == "fundamentals"
    assert InvestigationHost.cycle_investigation_id(["only"], "only", 1) is None


def test_multi_inv_workspaces_stay_independent(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        investigation_title="Fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai",
        investigation_id="peers",
        title="Peers",
        app_dir=tmp_path,
    )
    fund = load_project_investigation("ftai", "fundamentals", app_dir=tmp_path)
    peers = load_project_investigation("ftai", "peers", app_dir=tmp_path)
    fund.workspace.add_entity("united-states")
    peers.workspace.add_entity("cik:320193")
    persist_investigation(fund, app_dir=tmp_path)
    persist_investigation(peers, app_dir=tmp_path)

    fund2 = load_project_investigation("ftai", "fundamentals", app_dir=tmp_path)
    peers2 = load_project_investigation("ftai", "peers", app_dir=tmp_path)
    assert "united-states" in fund2.workspace.entity
    assert "cik:320193" not in fund2.workspace.entity
    assert "cik:320193" in peers2.workspace.entity
    assert "united-states" not in peers2.workspace.entity

    project = load_project("ftai", app_dir=tmp_path)
    assert project.investigations == ["fundamentals", "peers"]
    nxt = InvestigationHost.cycle_investigation_id(
        project.investigations, "fundamentals", 1
    )
    assert nxt == "peers"
    set_active("peers", project="ftai", action=ROOT_ACTION_ID, app_dir=tmp_path)
    active = load_active(app_dir=tmp_path)
    assert active.investigation == "peers"
    assert active.project == "ftai"


def test_reorder_and_move_investigation(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="a",
        app_dir=tmp_path,
    )
    add_investigation_to_project("ftai", investigation_id="b", app_dir=tmp_path)
    add_investigation_to_project("ftai", investigation_id="c", app_dir=tmp_path)

    project = reorder_investigations(
        "ftai", ["c", "a", "b"], app_dir=tmp_path
    )
    assert project.investigations == ["c", "a", "b"]

    project = move_investigation("ftai", "a", delta=-1, app_dir=tmp_path)
    assert project.investigations == ["a", "c", "b"]

    with pytest.raises(InvestigationError, match="exactly once"):
        reorder_investigations("ftai", ["a", "b"], app_dir=tmp_path)


def test_clone_project_copies_all_tabs_independently(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI Aviation",
        notes="source notes",
        investigation_id="fundamentals",
        investigation_title="Fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai",
        investigation_id="peers",
        title="Peers",
        app_dir=tmp_path,
    )
    fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    fund.workspace.add_metric("gdp")
    fund.workspace.add_entity("united-states")
    persist_investigation(fund, app_dir=tmp_path)

    cloned = clone_project(
        "ftai",
        title="FTAI Aviation (branched)",
        project_id="ftai-branched",
        app_dir=tmp_path,
    )
    assert cloned.id == "ftai-branched"
    assert cloned.title == "FTAI Aviation (branched)"
    assert cloned.notes == "source notes"
    assert cloned.investigations == ["fundamentals", "peers"]

    copy_fund = load_project_investigation(
        "ftai-branched", "fundamentals", app_dir=tmp_path
    )
    assert "gdp" in copy_fund.workspace.metric
    assert copy_fund.title == "Fundamentals"

    # Independence: mutate clone, source stays put.
    copy_fund.workspace.add_entity("france")
    persist_investigation(
        copy_fund, app_dir=tmp_path, project_id="ftai-branched"
    )
    source_fund = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    assert "france" not in source_fund.workspace.entity

    with pytest.raises(InvestigationError, match="already exists"):
        clone_project(
            "ftai", project_id="ftai-branched", app_dir=tmp_path
        )


def test_clone_project_default_title_and_id(tmp_path: Path):
    create_project_with_investigation(
        project_id="housing",
        title="Housing Recovery",
        app_dir=tmp_path,
    )
    cloned = clone_project("housing", app_dir=tmp_path)
    assert cloned.title == "Housing Recovery (branched)"
    assert cloned.id.startswith("housing-recovery-branched")
    assert cloned.investigations == ["main"]


def test_cli_clone_local_project(tmp_path: Path, monkeypatch):
    from click.testing import CliRunner

    from jstdata import cli as cli_module
    from jstdata.investigation import active

    monkeypatch.setattr("jstdata.client.APP_DIR", tmp_path)
    monkeypatch.setattr(active, "APP_DIR", tmp_path)
    create_project_with_investigation(
        project_id="housing",
        title="Housing Recovery",
        investigation_id="main",
        app_dir=tmp_path,
    )
    fund = load_project_investigation("housing", "main", app_dir=tmp_path)
    fund.workspace.add_metric("gdp")
    persist_investigation(fund, app_dir=tmp_path)

    runner = CliRunner()
    # Non-TTY uses the default ``(branched)`` title; --as sets the project id.
    result = runner.invoke(
        cli_module.cli,
        ["clone", "housing", "--as", "housing-branch"],
    )
    assert result.exit_code == 0, result.output
    assert "Housing Recovery (branched)" in result.output
    assert "housing-branch" in result.output

    cloned = load_project("housing-branch", app_dir=tmp_path)
    assert cloned.title == "Housing Recovery (branched)"
    copy = load_project_investigation(
        "housing-branch", "main", app_dir=tmp_path
    )
    assert "gdp" in copy.workspace.metric
    active_ctx = load_active(app_dir=tmp_path)
    assert active_ctx.project == "housing-branch"
    assert active_ctx.investigation == "main"


def test_duplicate_investigation_copies_workspace(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        investigation_title="Fundamentals",
        app_dir=tmp_path,
    )
    source = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    source.workspace.add_metric("gdp")
    source.workspace.add_entity("united-states")
    source.notes = "keep me"
    persist_investigation(source, app_dir=tmp_path)

    clone = duplicate_investigation(
        "ftai",
        "fundamentals",
        investigation_id="fundamentals-copy",
        title="Fundamentals copy",
        app_dir=tmp_path,
    )
    assert clone.id == "fundamentals-copy"
    assert clone.title == "Fundamentals copy"
    assert "gdp" in clone.workspace.metric
    assert "united-states" in clone.workspace.entity
    assert clone.notes == "keep me"

    project = load_project("ftai", app_dir=tmp_path)
    assert project.investigations == ["fundamentals", "fundamentals-copy"]

    # Edits to the clone must not mutate the source.
    clone.workspace.add_entity("france")
    persist_investigation(clone, app_dir=tmp_path)
    source2 = load_project_investigation(
        "ftai", "fundamentals", app_dir=tmp_path
    )
    assert "france" not in source2.workspace.entity


def test_remove_investigation_refuses_last_and_fixes_active(tmp_path: Path):
    create_project_with_investigation(
        project_id="solo",
        title="Solo",
        investigation_id="only",
        app_dir=tmp_path,
    )
    with pytest.raises(InvestigationError, match="last investigation"):
        remove_investigation_from_project("solo", "only", app_dir=tmp_path)

    create_project_with_investigation(
        project_id="ftai",
        title="FTAI",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai", investigation_id="peers", app_dir=tmp_path
    )
    set_active("peers", project="ftai", app_dir=tmp_path)

    remove_investigation_from_project("ftai", "peers", app_dir=tmp_path)
    project = load_project("ftai", app_dir=tmp_path)
    assert project.investigations == ["fundamentals"]
    assert not (
        project_investigations_dir("ftai", app_dir=tmp_path) / "peers.yaml"
    ).is_file()
    active = load_active(app_dir=tmp_path)
    assert active.investigation == "fundamentals"
    assert active.project == "ftai"


def test_parse_tab_commands():
    assert parse_command_line("tab").options["op"] == "next"
    assert parse_command_line("tab prev").options["op"] == "prev"
    assert parse_command_line("tab left").options["op"] == "left"
    parsed = parse_command_line('tab new --id peers "Peer set"')
    assert parsed.options["op"] == "new"
    assert parsed.options["investigation_id"] == "peers"
    assert parsed.options["title"] == "Peer set"
    rm = parse_command_line("tab rm --force")
    assert rm.options["op"] == "rm"
    assert rm.options["force"] is True
    with pytest.raises(CommandError):
        parse_command_line("tab nope")
