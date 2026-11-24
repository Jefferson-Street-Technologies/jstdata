"""Project directory layout, batch writer, and legacy migration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jstdata.investigation import (
    Investigation,
    Project,
    ActiveContext,
    add_investigation_to_project,
    create_investigation,
    create_project_with_investigation,
    ensure_bootstrap,
    get_investigation,
    list_projects,
    load_project,
    load_project_investigation,
    migrate_legacy_investigations,
    open_investigation,
    persist_investigation,
    save_investigation,
    set_active,
)
from jstdata.investigation.active import load_active, save_active
from jstdata.investigation.batch_writer import ProjectBatchWriter, ProjectLockError
from jstdata.investigation.project_store import (
    project_dir,
    project_investigations_dir,
    project_yaml_path,
)


def test_project_round_trip(tmp_path: Path):
    project, inv = create_project_with_investigation(
        project_id="ftai-aviation",
        title="FTAI Aviation",
        notes="peer research",
        investigation_id="fundamentals",
        investigation_title="Fundamentals",
        app_dir=tmp_path,
    )
    assert project.id == "ftai-aviation"
    assert project.investigations == ["fundamentals"]
    assert inv.id == "fundamentals"

    loaded = load_project("ftai-aviation", app_dir=tmp_path)
    assert loaded.title == "FTAI Aviation"
    assert loaded.notes == "peer research"

    yaml_path = project_yaml_path("ftai-aviation", app_dir=tmp_path)
    assert yaml_path.is_file()
    data = yaml.safe_load(yaml_path.read_text())
    assert data["investigations"] == ["fundamentals"]

    inv_path = project_investigations_dir("ftai-aviation", app_dir=tmp_path) / "fundamentals.yaml"
    assert inv_path.is_file()
    assert "workspace" in yaml.safe_load(inv_path.read_text())


def test_add_investigation_tab(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai-aviation",
        title="FTAI Aviation",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    peers = add_investigation_to_project(
        "ftai-aviation",
        investigation_id="peers",
        title="Peers",
        app_dir=tmp_path,
    )
    assert peers.id == "peers"
    project = load_project("ftai-aviation", app_dir=tmp_path)
    assert project.investigations == ["fundamentals", "peers"]


def test_batch_writer_atomic(tmp_path: Path):
    target = tmp_path / "projects" / "demo"
    inv = Investigation.new("main", title="Main")
    project = Project.new("demo", title="Demo", investigation_ids=["main"])
    with ProjectBatchWriter(target) as writer:
        writer.stage_project(project)
        writer.stage_investigation(inv)
    assert (target / "project.yaml").is_file()
    assert (target / "investigations" / "main.yaml").is_file()
    assert not (target / ".lock").exists()


def test_batch_writer_lock_timeout(tmp_path: Path):
    target = tmp_path / "projects" / "locked"
    target.mkdir(parents=True)
    (target / ".lock").write_text("1\n9999999999\n", encoding="utf-8")
    with pytest.raises(ProjectLockError, match="Timed out"):
        with ProjectBatchWriter(target, lock_timeout=0.1):
            pass


def test_migrate_legacy_singleton(tmp_path: Path):
    legacy = tmp_path / "investigations"
    inv = Investigation.new("housing-slowdown", title="Housing")
    save_investigation(inv, root=legacy)
    assert (legacy / "housing-slowdown.yaml").is_file()

    migrated = migrate_legacy_investigations(app_dir=tmp_path)
    assert "housing-slowdown" in migrated
    assert not (legacy / "housing-slowdown.yaml").exists()
    assert project_yaml_path("housing-slowdown", app_dir=tmp_path).is_file()
    loaded = load_project_investigation(
        "housing-slowdown", "housing-slowdown", app_dir=tmp_path
    )
    assert loaded.title == "Housing"

    # Idempotent
    assert migrate_legacy_investigations(app_dir=tmp_path) == []


def test_create_investigation_makes_project(tmp_path: Path):
    inv = create_investigation(
        "housing-slowdown", title="Housing", app_dir=tmp_path
    )
    assert inv.id == "housing-slowdown"
    assert "housing-slowdown" in list_projects(app_dir=tmp_path)
    ctx = load_active(app_dir=tmp_path)
    assert ctx.project == "housing-slowdown"
    assert ctx.investigation == "housing-slowdown"


def test_create_untitled_is_one_to_one_project(tmp_path: Path):
    inv = create_investigation(app_dir=tmp_path)
    assert inv.title == "Untitled"
    ctx = load_active(app_dir=tmp_path)
    assert ctx.project == inv.id
    assert ctx.investigation == inv.id
    assert ctx.project.startswith("proj-") or ctx.project.startswith("untitled")


def test_open_by_project_id(tmp_path: Path):
    create_project_with_investigation(
        project_id="ftai-aviation",
        title="FTAI Aviation",
        investigation_id="fundamentals",
        app_dir=tmp_path,
    )
    add_investigation_to_project(
        "ftai-aviation", investigation_id="peers", title="Peers", app_dir=tmp_path
    )
    inv = open_investigation("ftai-aviation", app_dir=tmp_path)
    assert inv.id == "fundamentals"
    ctx = load_active(app_dir=tmp_path)
    assert ctx.project == "ftai-aviation"


def test_persist_round_trip(tmp_path: Path):
    inv = create_investigation("demo", title="Demo", app_dir=tmp_path)
    inv.title = "Renamed"
    persist_investigation(inv, app_dir=tmp_path)
    loaded = get_investigation("demo", app_dir=tmp_path)
    assert loaded.title == "Renamed"


def test_active_context_optional_project(tmp_path: Path):
    # Legacy shape without project still loads.
    save_active(
        ActiveContext(investigation="demo", workspace=0, action=0),
        app_dir=tmp_path,
    )
    ctx = load_active(app_dir=tmp_path)
    assert ctx.project is None
    assert ctx.investigation == "demo"


def test_bootstrap_installs_bundled_projects(tmp_path: Path):
    tutorial, _ = ensure_bootstrap(app_dir=tmp_path)
    assert tutorial.id == "tutorial"
    assert "tutorial" in list_projects(app_dir=tmp_path)
    assert "plain-bagel" in list_projects(app_dir=tmp_path)
    # Legacy flat copies should not remain as the source of truth.
    assert not (tmp_path / "investigations" / "tutorial.yaml").exists()


def test_set_active_resolves_project(tmp_path: Path):
    create_investigation("demo", app_dir=tmp_path)
    ctx = set_active("demo", app_dir=tmp_path)
    assert ctx.project == "demo"
