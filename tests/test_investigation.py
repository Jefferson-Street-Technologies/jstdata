"""Tests for investigation model, store, active context, and API."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from jstdata.cli import cli
from jstdata.investigation import (
    DEFAULT_WORKSPACE_ID,
    ROOT_ACTION_ID,
    Action,
    ActiveContext,
    Investigation,
    InvestigationError,
    TUTORIAL_ID,
    Workspace,
    add_action,
    create_investigation,
    ensure_bootstrap,
    get_active,
    get_investigation,
    load_bundled_tutorial,
    load_investigation,
    open_investigation,
    remove_action,
    save_investigation,
    validate_investigation_id,
)
from jstdata.investigation.active import load_active, save_active
from jstdata.actions.base import get_action


def test_validate_investigation_id():
    assert validate_investigation_id("housing-slowdown") == "housing-slowdown"
    with pytest.raises(InvestigationError, match="slug"):
        validate_investigation_id("Housing")
    with pytest.raises(InvestigationError, match="slug"):
        validate_investigation_id("housing_slowdown")


def test_investigation_new_and_round_trip(tmp_path: Path):
    inv = Investigation.new("demo", title="Demo Title")
    assert inv.title == "Demo Title"
    assert DEFAULT_WORKSPACE_ID in inv.workspaces
    assert ROOT_ACTION_ID in inv.actions
    assert inv.actions[ROOT_ACTION_ID].type == "home"
    assert inv.actions[ROOT_ACTION_ID].parent_id is None

    root = tmp_path / "investigations"
    path = save_investigation(inv, root=root)
    assert path.name == "demo.yaml"
    loaded = load_investigation("demo", root=root)
    assert loaded.title == "Demo Title"
    assert loaded.workspaces[0].id == 0


def test_bfs_order_and_cascade_delete():
    inv = Investigation.new("tree")
    a1 = inv.add_action("console", parent_id=ROOT_ACTION_ID, args={"taxonomy": "country"})
    a2 = inv.add_action("discover", parent_id=ROOT_ACTION_ID)
    a3 = inv.add_action("rank", parent_id=a1.id, args={"taxonomy": "country"})
    assert inv.bfs_order() == [ROOT_ACTION_ID, a1.id, a2.id, a3.id]

    removed = inv.remove_action_cascade(a1.id)
    assert set(removed) == {a1.id, a3.id}
    assert inv.bfs_order() == [ROOT_ACTION_ID, a2.id]
    with pytest.raises(InvestigationError, match="root"):
        inv.remove_action_cascade(ROOT_ACTION_ID)


def test_workspace_order():
    inv = Investigation.new("multi-ws")
    inv.workspaces[2] = Workspace(id=2, label="B")
    inv.workspaces[1] = Workspace(id=1, label="A")
    assert inv.workspace_order() == [0, 1, 2]


def test_workspace_session_projection():
    ws = Workspace(
        id=0,
        label="Main",
        description="scratch",
        metric=["gdp"],
        entity=["france"],
        taxonomy="country",
    )
    assert ws.metric == ["gdp"]
    assert ws.entity == ["france"]
    assert ws.taxonomy == "country"
    assert ws.add_metric("cpi") is True
    assert ws.metric == ["gdp", "cpi"]
    assert "gdp" in ws.to_query_kwargs()["metric"]


def test_tree_validation_rejects_orphan_and_cycle():
    with pytest.raises(InvestigationError, match="root"):
        Investigation(
            id="bad",
            title="bad",
            workspaces={0: Workspace(id=0)},
            actions={1: Action(id=1, type="home", parent_id=None)},
        ).validate()

    inv = Investigation.new("cyc")
    inv.actions[1] = Action(id=1, type="console", parent_id=0)
    inv.actions[2] = Action(id=2, type="console", parent_id=1)
    inv.actions[1] = Action(id=1, type="console", parent_id=2)
    with pytest.raises(InvestigationError, match="Cycle"):
        inv.validate()


def test_ensure_bootstrap_and_active(tmp_path: Path):
    tutorial, ctx = ensure_bootstrap(app_dir=tmp_path)
    assert tutorial.id == TUTORIAL_ID
    assert (tmp_path / "investigations" / "tutorial.yaml").is_file()
    assert (tmp_path / ".active.yaml").is_file()
    assert ctx.investigation == TUTORIAL_ID
    assert ctx.workspace == 0
    assert ctx.action == 0

    bundled = load_bundled_tutorial()
    assert bundled.actions[0].type == "home"
    assert "Jefferson Street" in (bundled.actions[0].description or "")


def test_create_and_open_api(tmp_path: Path):
    ensure_bootstrap(app_dir=tmp_path)
    inv = create_investigation("housing", title="Housing", app_dir=tmp_path)
    assert inv.title == "Housing"
    active = get_active(app_dir=tmp_path)
    assert active.investigation == "housing"
    assert active.action == ROOT_ACTION_ID

    with pytest.raises(InvestigationError, match="already exists"):
        create_investigation("housing", app_dir=tmp_path)

    # Add a child via API; activate flag writes .active.yaml
    child = add_action(
        inv,
        "console",
        args={"taxonomy": "country"},
        description="Browse countries",
        activate=True,
        app_dir=tmp_path,
    )
    assert get_active(app_dir=tmp_path).action == child.id
    assert inv.actions[child.id].parent_id == ROOT_ACTION_ID

    # Re-open preserves active action when still valid
    open_investigation("housing", app_dir=tmp_path)
    assert get_active(app_dir=tmp_path).action == child.id

    remove_action(inv, child.id, force=True, app_dir=tmp_path)
    assert child.id not in inv.actions
    assert get_active(app_dir=tmp_path).action == ROOT_ACTION_ID


def test_remove_requires_force_for_active_and_children(tmp_path: Path):
    ensure_bootstrap(app_dir=tmp_path)
    inv = create_investigation("force-rm", app_dir=tmp_path)
    parent = add_action(inv, "console", app_dir=tmp_path)
    child = add_action(inv, "discover", parent_id=parent.id, app_dir=tmp_path)

    with pytest.raises(InvestigationError, match="descendant"):
        remove_action(inv, parent.id, app_dir=tmp_path)

    # Activate leaf, then refuse without --force
    from jstdata.investigation import set_active

    set_active("force-rm", action=child.id, app_dir=tmp_path)
    with pytest.raises(InvestigationError, match="active action"):
        remove_action(inv, child.id, app_dir=tmp_path)

    removed = remove_action(inv, parent.id, force=True, app_dir=tmp_path)
    assert set(removed) == {parent.id, child.id}
    assert get_active(app_dir=tmp_path).action == ROOT_ACTION_ID


def test_parse_action_args_json():
    from jstdata.investigation import parse_action_args_json

    assert parse_action_args_json(None) == {}
    assert parse_action_args_json('{"taxonomy":"country"}') == {"taxonomy": "country"}
    with pytest.raises(InvestigationError, match="JSON"):
        parse_action_args_json("{")
    with pytest.raises(InvestigationError, match="object"):
        parse_action_args_json("[1,2]")


def test_cli_ls_add_rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from jstdata import investigation as inv_pkg
    from jstdata.investigation import api as api_mod

    def with_tmp(fn):
        def wrapped(*args, **kwargs):
            kwargs.setdefault("app_dir", tmp_path)
            return fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(inv_pkg, "ensure_bootstrap", with_tmp(api_mod.ensure_bootstrap))
    monkeypatch.setattr(inv_pkg, "get_active", with_tmp(api_mod.get_active))
    monkeypatch.setattr(
        inv_pkg, "list_investigations",
        lambda **kw: api_mod.list_investigations(root=tmp_path / "investigations"),
    )
    monkeypatch.setattr(
        "jstdata.investigation.store.investigations_dir",
        lambda **kw: tmp_path / "investigations",
    )
    monkeypatch.setattr(
        inv_pkg,
        "load_investigation",
        lambda iid, root=None, **kw: api_mod.get_investigation(iid, app_dir=tmp_path),
    )
    monkeypatch.setattr(
        inv_pkg,
        "require_active_investigation",
        with_tmp(api_mod.require_active_investigation),
    )
    monkeypatch.setattr(inv_pkg, "add_action", with_tmp(api_mod.add_action))
    monkeypatch.setattr(inv_pkg, "remove_action", with_tmp(api_mod.remove_action))
    monkeypatch.setattr(inv_pkg, "parse_action_args_json", api_mod.parse_action_args_json)

    ensure_bootstrap(app_dir=tmp_path)
    create_investigation("alpha", title="Alpha", app_dir=tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["investigation", "ls"])
    assert result.exit_code == 0, result.output
    assert "* alpha" in result.output
    assert "tutorial" in result.output

    result = runner.invoke(
        cli,
        [
            "action",
            "add",
            "console",
            "--args",
            '{"taxonomy":"country"}',
            "--description",
            "browse",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Added action 1 type=console parent=0" in result.output
    inv = get_investigation("alpha", app_dir=tmp_path)
    assert inv.actions[1].args == {"taxonomy": "country"}
    assert get_active(app_dir=tmp_path).action == ROOT_ACTION_ID

    result = runner.invoke(cli, ["action", "add", "discover", "--activate"])
    assert result.exit_code == 0, result.output
    assert "(activated)" in result.output
    assert get_active(app_dir=tmp_path).action == 2

    result = runner.invoke(cli, ["action", "rm", "2"])
    assert result.exit_code == 2
    assert "--force" in result.output

    result = runner.invoke(cli, ["action", "rm", "2", "--force"])
    assert result.exit_code == 0, result.output
    assert "Removed action 2" in result.output
    assert get_active(app_dir=tmp_path).action == ROOT_ACTION_ID

    result = runner.invoke(cli, ["action", "add", "not-a-step"])
    assert result.exit_code == 2
    assert "Unknown action" in result.output


def test_home_step_registered():
    import jstdata.investigation  # noqa: F401

    spec = get_action("home")
    assert spec.id == "home"
    assert spec.name == "Home"


def test_cli_create_rejects_duplicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "jstdata.investigation.store.INVESTIGATIONS_DIR",
        tmp_path / "investigations",
    )
    monkeypatch.setattr(
        "jstdata.client.APP_DIR",
        tmp_path,
    )
    # Re-bind active/store paths that closed over APP_DIR at import time.
    import jstdata.investigation.active as active_mod
    import jstdata.investigation.store as store_mod
    import jstdata.investigation.api as api_mod

    monkeypatch.setattr(store_mod, "INVESTIGATIONS_DIR", tmp_path / "investigations")
    monkeypatch.setattr(active_mod, "APP_DIR", tmp_path)

    # API helpers use APP_DIR from client via active_path default — patch create path
    # by exercising API with app_dir instead of full TUI CLI.
    ensure_bootstrap(app_dir=tmp_path)
    create_investigation("once", app_dir=tmp_path)
    with pytest.raises(InvestigationError, match="already exists"):
        create_investigation("once", app_dir=tmp_path)


def test_cli_create_load_without_tui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """CLI create/load should fail fast on errors; success path launches TUI.

    We stub the host so CliRunner can assert bootstrap side effects.
    """
    launched: list[str] = []

    def fake_run(client, *, app_dir=None):
        launched.append("run")

    monkeypatch.setattr(
        "jstdata.investigation.host.run_investigation_host", fake_run
    )
    monkeypatch.setattr(
        "jstdata.investigation.run_investigation_host", fake_run
    )

    # Point default app paths at tmp via app_dir injection in API called by CLI.
    # CLI doesn't pass app_dir — monkeypatch ensure/create/open/run.
    from jstdata import investigation as inv_pkg
    from jstdata.investigation import api as api_mod

    def create_in_tmp(investigation_id, *, title=None, app_dir=None, **kwargs):
        return api_mod.create_investigation(
            investigation_id, title=title, app_dir=tmp_path, activate=True
        )

    def open_in_tmp(investigation_id, *, app_dir=None, **kwargs):
        return api_mod.open_investigation(investigation_id, app_dir=tmp_path)

    monkeypatch.setattr(inv_pkg, "create_investigation", create_in_tmp)
    monkeypatch.setattr(inv_pkg, "open_investigation", open_in_tmp)
    monkeypatch.setattr(inv_pkg, "run_investigation_host", fake_run)

    runner = CliRunner()
    result = runner.invoke(cli, ["investigation", "create", "alpha", "--title", "Alpha"])
    assert result.exit_code == 0, result.output
    assert "Created investigation 'alpha'" in result.output
    assert launched == ["run"]
    assert (tmp_path / "investigations" / "alpha.yaml").is_file()
    assert load_active(app_dir=tmp_path).investigation == "alpha"

    launched.clear()
    result = runner.invoke(cli, ["investigation", "load", "alpha"])
    assert result.exit_code == 0, result.output
    assert "Loaded investigation 'alpha'" in result.output
    assert launched == ["run"]

    result = runner.invoke(cli, ["investigation", "load", "missing"])
    assert result.exit_code == 2
    assert "not found" in result.output.lower() or "Error" in result.output


def test_active_context_round_trip(tmp_path: Path):
    ctx = ActiveContext(investigation="tutorial", workspace=0, action=0)
    save_active(ctx, app_dir=tmp_path)
    raw = yaml.safe_load((tmp_path / ".active.yaml").read_text())
    assert raw == {"investigation": "tutorial", "workspace": 0, "action": 0}
    loaded = load_active(app_dir=tmp_path)
    assert loaded == ctx


def test_title_defaults_to_id():
    inv = Investigation.new("eu-defense")
    assert inv.title == "eu-defense"
