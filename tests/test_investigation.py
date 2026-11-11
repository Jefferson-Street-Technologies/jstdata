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
    Query,
    ResearchContext,
    TUTORIAL_ID,
    Workspace,
    add_action,
    create_investigation,
    ensure_bootstrap,
    focus_replace_context,
    get_active,
    get_investigation,
    load_bundled_tutorial,
    load_investigation,
    open_investigation,
    remove_action,
    resolve_input_context,
    resume_or_create_investigation,
    save_investigation,
    update_investigation,
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
    assert inv.workspace.id == DEFAULT_WORKSPACE_ID
    assert inv.queries == {}
    assert ROOT_ACTION_ID in inv.actions
    assert inv.actions[ROOT_ACTION_ID].type == "home"
    assert inv.actions[ROOT_ACTION_ID].parent_id is None

    root = tmp_path / "investigations"
    path = save_investigation(inv, root=root)
    assert path.name == "demo.yaml"
    loaded = load_investigation("demo", root=root)
    assert loaded.title == "Demo Title"
    assert loaded.workspace.id == 0
    assert "workspace" in yaml.safe_load(path.read_text())
    assert "workspaces" not in yaml.safe_load(path.read_text())


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
    inv = Investigation.new("one-ws")
    assert inv.workspace_order() == [DEFAULT_WORKSPACE_ID]
    assert inv.get_workspace(99) is inv.workspace


def test_legacy_workspaces_list_loads_first():
    inv = Investigation.from_dict(
        {
            "id": "legacy",
            "title": "legacy",
            "workspaces": [
                {"id": 0, "metric": ["gdp"], "entity": ["france"]},
                {"id": 1, "metric": ["cpi"]},
            ],
            "actions": [{"id": 0, "type": "home", "args": {}}],
        }
    )
    assert inv.workspace.metric == ["gdp"]
    assert inv.workspace.entity == ["france"]
    dumped = inv.to_dict()
    assert "workspace" in dumped
    assert "workspaces" not in dumped


def test_query_cartesian_kwargs_and_round_trip(tmp_path: Path):
    query = Query(
        id=1,
        name="FR vs US",
        metric=["gdp", "cpi"],
        entity=["france", "usa"],
        tail=1,
        sort_by="value",
        units="USD",
        source="BEA",
    )
    assert query.expected_pairs() == [
        ("gdp", "france"),
        ("gdp", "usa"),
        ("cpi", "france"),
        ("cpi", "usa"),
    ]
    kwargs = query.to_query_kwargs()
    assert kwargs["metric"] == ["gdp", "cpi"]
    assert kwargs["entity"] == ["france", "usa"]
    assert "units" not in kwargs
    assert "source" not in kwargs

    inv = Investigation.new("qdemo")
    inv.workspace.add_metric("gdp")
    inv.workspace.add_entity("france")
    saved = inv.add_query(from_workspace=True, name="scratch")
    assert saved.id == 1
    assert saved.metric == ["gdp"]
    assert saved.entity == ["france"]
    extra = inv.add_query(
        metric=["cpi"], entity=["usa", "gbr"], name="cpi", tail=1
    )
    assert extra.id == 2
    with pytest.raises(InvestigationError, match="metric, entity, or series"):
        inv.add_query()

    root = tmp_path / "investigations"
    save_investigation(inv, root=root)
    loaded = load_investigation("qdemo", root=root)
    assert loaded.get_query(1).name == "scratch"
    assert loaded.get_query(2).entity == ["usa", "gbr"]
    assert loaded.notes is None


def test_workspace_resource_helpers():
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


def test_resolve_input_context_focus_replace():
    ws = Workspace(
        id=0,
        metric=["gdp", "cpi"],
        entity=["france", "germany", "spain"],
        taxonomy="country",
    )
    # No overrides → full global bag.
    full = resolve_input_context(ws, None)
    assert full.metric == ["gdp", "cpi"]
    assert full.entity == ["france", "germany", "spain"]
    assert full.taxonomy == "country"

    # Focus entity; metrics + taxonomy inherit.
    focused = resolve_input_context(
        ws, ResearchContext(entity=["germany"])
    )
    assert focused.entity == ["germany"]
    assert focused.metric == ["gdp", "cpi"]
    assert focused.taxonomy == "country"

    # Parent metric override + child entity focus (table Enter² path).
    parent = ResearchContext(metric=["gdp"])
    child_overrides = focus_replace_context(parent, entity=["france"])
    assert child_overrides.metric == ["gdp"]
    assert child_overrides.entity == ["france"]
    resolved = resolve_input_context(ws, child_overrides)
    assert resolved.metric == ["gdp"]
    assert resolved.entity == ["france"]
    assert resolved.taxonomy == "country"

    # Release is view-local; focus_replace can set it for provenance drills.
    rel = focus_replace_context(
        parent, entity=["france"], release=["0001652044-25-000012"]
    )
    assert rel.release == ["0001652044-25-000012"]
    assert rel.metric == ["gdp"]


def test_action_context_yaml_round_trip(tmp_path: Path):
    inv = Investigation.new("ctx-demo")
    child = inv.add_action(
        "table",
        parent_id=ROOT_ACTION_ID,
        args={"sort_by": "value"},
        context=ResearchContext(entity=["alphabet"], metric=["long_term_debt"]),
        description="Alphabet LTD",
    )
    root = tmp_path / "investigations"
    save_investigation(inv, root=root)
    loaded = load_investigation("ctx-demo", root=root)
    action = loaded.get_action(child.id)
    assert action.type == "table"
    assert action.context is not None
    assert action.context.entity == ["alphabet"]
    assert action.context.metric == ["long_term_debt"]
    assert action.args == {"sort_by": "value"}

    # Empty context omitted from YAML.
    bare = inv.add_action("table", parent_id=ROOT_ACTION_ID)
    assert "context" not in bare.to_dict()


def test_add_action_persists_context(tmp_path: Path):
    ensure_bootstrap(app_dir=tmp_path)
    inv = create_investigation("bagel", app_dir=tmp_path)
    child = add_action(
        inv,
        "table",
        context=ResearchContext(entity=["msft"]),
        activate=True,
        app_dir=tmp_path,
    )
    reloaded = get_investigation("bagel", app_dir=tmp_path)
    assert reloaded.actions[child.id].context is not None
    assert reloaded.actions[child.id].context.entity == ["msft"]
    assert get_active(app_dir=tmp_path).action == child.id


def test_action_siblings_and_children():
    inv = Investigation.new("tree")
    a = inv.add_action("console", parent_id=0)
    b = inv.add_action("console", parent_id=0)
    c = inv.add_action("discover", parent_id=a.id)
    assert inv.sibling_ids(a.id) == [a.id, b.id]
    assert inv.sibling_ids(b.id) == [a.id, b.id]
    assert inv.sibling_ids(0) == [0]
    assert [x.id for x in inv.children(0)] == [a.id, b.id]
    assert [x.id for x in inv.children(a.id)] == [c.id]
    assert inv.siblings(c.id) == [c]


def test_tree_validation_rejects_orphan_and_cycle():
    with pytest.raises(InvestigationError, match="root"):
        Investigation(
            id="bad",
            title="bad",
            workspace=Workspace(id=0),
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
    assert ctx is None
    assert (tmp_path / "investigations" / "tutorial.yaml").is_file()
    assert (tmp_path / "investigations" / "plain-bagel.yaml").is_file()
    assert not (tmp_path / ".active.yaml").is_file()
    from jstdata.investigation import load_bundled_plain_bagel, PLAIN_BAGEL_ID

    demo = load_investigation(PLAIN_BAGEL_ID, root=tmp_path / "investigations")
    bundled = load_bundled_plain_bagel()
    assert demo.id == bundled.id
    assert any(a.type == "table" for a in demo.actions.values())
    assert "long-term-debt" in demo.workspace.metric
    assert 1 in demo.queries
    assert demo.queries[1].expected_pairs()

    bundled = load_bundled_tutorial()
    assert bundled.actions[0].type == "home"
    assert "Search" in (bundled.actions[0].description or "")
    assert bundled.workspace.label == "Scratch"


def test_resume_or_create_untitled(tmp_path: Path):
    inv = resume_or_create_investigation(app_dir=tmp_path)
    assert inv.title == "Untitled"
    assert inv.id.startswith("inv-")
    assert get_active(app_dir=tmp_path).investigation == inv.id
    again = resume_or_create_investigation(app_dir=tmp_path)
    assert again.id == inv.id


def test_create_and_open_api(tmp_path: Path):
    ensure_bootstrap(app_dir=tmp_path)
    untitled = create_investigation(app_dir=tmp_path)
    assert untitled.title == "Untitled"
    assert untitled.id.startswith("inv-")
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


def test_update_investigation_title_and_notes(tmp_path: Path):
    ensure_bootstrap(app_dir=tmp_path)
    inv = create_investigation("meta", title="Untitled", app_dir=tmp_path)
    update_investigation(
        inv,
        title="Plain Bagel",
        notes="EU semiconductor concentration",
        app_dir=tmp_path,
    )
    loaded = get_investigation("meta", app_dir=tmp_path)
    assert loaded.title == "Plain Bagel"
    assert loaded.notes == "EU semiconductor concentration"
    update_investigation(inv, notes="", app_dir=tmp_path)
    assert get_investigation("meta", app_dir=tmp_path).notes is None
    with pytest.raises(InvestigationError, match="empty"):
        update_investigation(inv, title="  ", app_dir=tmp_path)


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
    monkeypatch.setattr(inv_pkg, "add_query", with_tmp(api_mod.add_query))
    monkeypatch.setattr(inv_pkg, "remove_action", with_tmp(api_mod.remove_action))
    monkeypatch.setattr(inv_pkg, "parse_action_args_json", api_mod.parse_action_args_json)

    ensure_bootstrap(app_dir=tmp_path)
    create_investigation("alpha", title="Alpha", app_dir=tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["ls"])
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

    result = runner.invoke(
        cli,
        [
            "query",
            "add",
            "--metric",
            "gdp",
            "--entity",
            "france",
            "--entity",
            "usa",
            "--name",
            "FR vs US",
            "--tail",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Added query 1" in result.output
    stored = get_investigation("alpha", app_dir=tmp_path).get_query(1)
    assert stored.metric == ["gdp"]
    assert stored.entity == ["france", "usa"]
    assert stored.expected_pairs() == [("gdp", "france"), ("gdp", "usa")]

    result = runner.invoke(cli, ["query", "ls"])
    assert result.exit_code == 0, result.output
    assert "FR vs US" in result.output

    result = runner.invoke(cli, ["query", "show", "1"])
    assert result.exit_code == 0, result.output
    assert "gdp" in result.output
    assert "france" in result.output


def test_home_step_registered():
    import jstdata.investigation  # noqa: F401
    import jstdata.actions  # noqa: F401

    spec = get_action("home")
    assert spec.id == "home"
    assert spec.name == "Home"
    query = get_action("query")
    assert query.id == "query"


def test_home_destinations_are_five_in_order():
    from jstdata.investigation.nav import DEST_IDS, DESTINATIONS, inspector_copy, workspace_counts_line

    assert [d.id for d in DESTINATIONS] == list(DEST_IDS)
    assert DEST_IDS == ("recent", "search", "actions", "workspace", "queries")
    inv = Investigation.new("demo", title="Hyperscaler")
    inv.workspace.add_metric("gdp")
    inv.workspace.add_entity("france")
    inv.add_query(from_workspace=True, name="FR GDP")
    assert workspace_counts_line(inv) == "1M · 1E · 0S · 1Q"
    copy = inspector_copy(DESTINATIONS[4], inv)
    assert "1 query" in copy
    assert "SAVED" in copy
    assert "Search" in DESTINATIONS[1].title
    fresh = Investigation.new("fresh")
    recent = inspector_copy(DESTINATIONS[0], fresh)
    assert "Newest first" in recent
    assert "No recent updates available" not in recent
    assert "unified" not in recent.lower()
    assert "Start exploring" in recent
    assert "guided tutorial" in recent
    assert "Start exploring" not in copy


def test_home_recent_pagination_state():
    from unittest.mock import MagicMock

    from jstdata.actions.pagination import page_fetch_limit, take_page
    from jstdata.investigation.home import RECENT_PAGE, HomeScreen, ReleaseTile
    from jstdata.models import Release
    from datetime import date

    screen = HomeScreen(client=MagicMock(), workspace=Workspace())
    assert screen._recent_offset == 0
    assert screen._recent_has_more is False
    assert RECENT_PAGE == 20
    page, more = take_page(list(range(page_fetch_limit(RECENT_PAGE))), RECENT_PAGE)
    assert len(page) == RECENT_PAGE
    assert more is True
    tile = ReleaseTile(
        Release(
            id="bea-2024-02-01",
            label="GDP (2024-02-01)",
            release_date=date(2024, 2, 1),
            source="BEA",
        )
    )
    assert tile.release.id == "bea-2024-02-01"


def test_home_refresh_live_before_mount_does_not_raise():
    from jstdata.investigation.home import HomeScreen

    screen = HomeScreen(client=object(), workspace=Workspace())  # type: ignore[arg-type]
    screen.refresh_live()


def test_action_graph_renders_real_tree_and_stays_unlabeled():
    import re

    from jstdata.investigation.nav import render_action_graph

    def plain(art: str) -> str:
        return re.sub(r"\[/?[^\]]*\]", "", art)

    inv = Investigation.new("graph")
    art = render_action_graph(inv, width=40, height=8)
    empty = plain(art)
    assert "JST" not in empty
    assert "●" in empty
    assert "/" not in empty and "\\" not in empty
    assert "·" not in empty
    assert "#4ade80" in art or "#86efac" in art or "#22c55e" in art
    assert "RELATIONSHIP" not in art
    assert "SIGNATURE" not in art

    parent = inv.add_action("console", parent_id=ROOT_ACTION_ID, description="Search")
    inv.add_action("table", parent_id=parent.id, description="Debt")
    inv.add_action("metric", parent_id=parent.id, description="LTD")
    grown = plain(render_action_graph(inv, width=48, height=10, active_id=parent.id))
    assert "JST" not in grown
    assert "●" in grown or "•" in grown
    assert "─" in grown
    assert "│" in grown
    assert "/" not in grown and "\\" not in grown

    fan = Investigation.new("fan")
    for i in range(5):
        fan.add_action("console", parent_id=ROOT_ACTION_ID, description=str(i))
    burst = plain(render_action_graph(fan, width=48, height=9))
    assert "─" in burst
    assert "│" in burst
    assert "/" not in burst and "\\" not in burst


def test_action_tree_is_a_tree_and_shrinks_with_size():
    from jstdata.investigation.nav import action_tree_rows, tree_row_height

    inv = Investigation.new("tree")
    sparse = action_tree_rows(inv)
    assert sparse[0].action_id == ROOT_ACTION_ID
    assert sparse[0].type == "home"
    assert tree_row_height(len(sparse)) == 6

    parent = inv.add_action("console", parent_id=ROOT_ACTION_ID, description="Search")
    child = inv.add_action("metric", parent_id=parent.id, description="Long-term debt")
    inv.add_action("entity", parent_id=parent.id, description="Microsoft")
    rows = action_tree_rows(inv)
    titles = [r.title() for r in rows]
    assert any("Search" in t and "console" in t for t in titles)
    assert any("└─" in r.prefix or "├─" in r.prefix for r in rows)
    assert any(r.action_id == child.id for r in rows)
    assert tree_row_height(len(rows)) < 6
    assert tree_row_height(24) == 1
    # Dense tree: every node is one cell.
    for i in range(20):
        inv.add_action("table", parent_id=ROOT_ACTION_ID, description=f"t{i}")
    assert tree_row_height(len(action_tree_rows(inv))) == 1


def test_remove_query_drops_query_and_its_views(tmp_path: Path):
    from jstdata.investigation.api import add_query, remove_query

    app_dir = tmp_path
    inv = create_investigation("demo", app_dir=app_dir)
    inv.workspace.add_metric("gdp")
    inv.workspace.add_entity("france")
    q = add_query(inv, from_workspace=True, name="FR GDP", app_dir=app_dir)
    add_action(
        inv,
        "query",
        args={"query_id": q.id},
        parent_id=ROOT_ACTION_ID,
        app_dir=app_dir,
    )
    removed = remove_query(inv, q.id, app_dir=app_dir)
    assert removed.id == q.id
    assert q.id not in inv.queries
    assert not any(
        a.type == "query" and a.args.get("query_id") == q.id
        for a in inv.actions.values()
    )


def test_stage_ids_collects_into_workspace():
    from jstdata.actions.base import stage_ids

    class FakeApp:
        def __init__(self):
            self.labels = {}
            self.persisted = False

        def remember_label(self, rid, label):
            self.labels[rid] = label

        def persist_active_workspace(self):
            self.persisted = True

    ws = Workspace()
    app = FakeApp()
    added = stage_ids(ws, app, metric="gdp", entity="france", labels={"gdp": "GDP"})
    assert added == ["gdp", "france"]
    assert ws.metric == ["gdp"]
    assert ws.entity == ["france"]
    assert app.labels["gdp"] == "GDP"
    assert app.persisted is True
    assert stage_ids(ws, app, metric="gdp") == []


def test_search_inspector_and_series_scan_column():
    from datetime import datetime

    from jstdata.investigation.search_drawer import (
        clustered_search_items,
        resource_inspector_copy,
        result_primary_name,
    )
    from jstdata.models import Entity, Metric, Series

    france = Entity(id="france", label="France")
    spain = Entity(id="spain", label="Spain")
    series = Series(
        id="HDTGPDFRA163N",
        label="Household Debt to GDP for France",
        frequency="Quarterly",
        source="FRED",
        units="Percent of GDP",
        seasonal_adjustment="NSA",
        last_updated=datetime(2024, 1, 1),
        metric_id="household-debt-to-gdp",
        entities=[france],
    )
    spain_s = Series(
        id="HDTGPDESA163N",
        label="Household Debt to GDP for Spain",
        frequency="Quarterly",
        source="FRED",
        units="Percent of GDP",
        seasonal_adjustment="NSA",
        last_updated=datetime(2024, 1, 1),
        metric_id="household-debt-to-gdp",
        entities=[spain],
    )
    assert result_primary_name(series, "series", prefer_entity=True) == "France"
    assert result_primary_name(series, "series") == "Household Debt to GDP for France"
    copy = resource_inspector_copy(series, "series")
    assert "Household Debt to GDP for France" in copy
    assert "SOURCE" in copy
    assert "AVAILABLE ACTIONS" in copy
    assert "Type two or more" not in copy
    metric = Metric(id="gdp", name="GDP", description="Gross domestic product")
    items = clustered_search_items(
        [(metric, "metric"), (series, "series"), (spain_s, "series")]
    )
    kinds = [k for k, _, _ in items]
    headers = [p for k, p, _ in items if k == "header"]
    assert kinds[0] == "header"
    assert "METRIC" in headers
    assert "HOUSEHOLD DEBT TO GDP" in headers
    assert any(k == "row" and r == series for k, r, _ in items)

    bank = Series(
        id="DDSI01FRA645NWDB",
        label="Bank Z-Score for France",
        frequency="Annual",
        source="FRED",
        units="Z-score",
        seasonal_adjustment="NSA",
        last_updated=datetime(2024, 1, 1),
        metric_id="bank-z-score",
        entities=[france],
    )
    migration = Series(
        id="SMPOPNETMFRA",
        label="Net migration for France",
        frequency="Annual",
        source="FRED",
        units="People",
        seasonal_adjustment="NSA",
        last_updated=datetime(2024, 1, 1),
        metric_id="net-migration",
        entities=[france],
    )
    mixed = clustered_search_items(
        [(france, "entity"), (bank, "series"), (migration, "series")]
    )
    mixed_headers = [p for k, p, _ in mixed if k == "header"]
    assert mixed_headers == ["ENTITY", "SERIES"]
    assert "BANK Z-SCORE" not in mixed_headers

    unkeyed = clustered_search_items(
        [
            (france, "entity"),
            (
                Series(
                    id="DDSI01FRA645NWDB",
                    label="Bank Z-Score for France",
                    frequency="Annual",
                    source="FRED",
                    units="Z-score",
                    seasonal_adjustment="NSA",
                    last_updated=datetime(2024, 1, 1),
                    metric_id="",
                    entities=[france],
                ),
                "series",
            ),
            (
                Series(
                    id="SMPOPNETMFRA",
                    label="Net migration for France",
                    frequency="Annual",
                    source="FRED",
                    units="People",
                    seasonal_adjustment="NSA",
                    last_updated=datetime(2024, 1, 1),
                    metric_id="",
                    entities=[france],
                ),
                "series",
            ),
        ]
    )
    assert [p for k, p, _ in unkeyed if k == "header"] == ["ENTITY", "SERIES"]


def test_query_formats_periods_and_values():
    from datetime import datetime

    from textual_plotext import PlotextPlot

    from jstdata.actions.query_view import (
        format_period,
        format_release,
        format_value,
        history_points,
        observation_detail_copy,
        plot_history,
        query_spec_line,
        record_title,
    )
    from jstdata.investigation.model import Query

    stamp = datetime(2024, 1, 1)
    assert format_period(stamp, "Quarterly") == "2024 Q1"
    assert format_period(stamp, "Annual") == "2024"
    assert format_release(datetime(2024, 8, 1)) == "2024-08-01"
    assert format_release(datetime(2024, 8, 1), short=True) == "Aug 2024"
    assert format_value(71.5228842446577) == "71.52"
    assert format_value(None) == "—"
    record = {
        "id": "HDTGPDUSQ163N",
        "label": "Household debt to GDP",
        "metric_id": "household-debt-to-gdp",
        "entities": [{"id": "united-states", "label": "United States"}],
        "frequency": "Quarterly",
        "units": "Ratio",
        "source": "FSI",
        "n_observations": 2,
        "last": {
            "observation_timestamp": "2024-01-01T00:00:00",
            "release_timestamp": "2024-08-01T00:00:00",
            "value": 71.522,
            "release_id": "r1",
        },
        "observations": [
            {
                "observation_timestamp": "2023-10-01T00:00:00",
                "release_timestamp": "2024-05-01T00:00:00",
                "value": 70.1,
                "release_id": "r0",
            },
            {
                "observation_timestamp": "2024-01-01T00:00:00",
                "release_timestamp": "2024-08-01T00:00:00",
                "value": 71.522,
                "release_id": "r1",
            },
        ],
    }
    assert "United States" in record_title(record)
    detail = observation_detail_copy(record, record["last"])
    assert "2024 Q1" in detail
    assert "71.52" in detail
    assert "HDTGPDUSQ163N" in detail
    times, values = history_points(record)
    assert times == ["2023-10-01", "2024-01-01"]
    assert values == [70.1, 71.522]
    plt = PlotextPlot().plt
    assert plot_history(plt, record) is True
    plt.plotsize(48, 12)
    chart = plt.uncolorize(plt.build())
    assert "70.1" in chart
    assert "Ratio" in chart
    assert plot_history(plt, {**record, "observations": []}) is False
    spec = query_spec_line(
        Query(id=1, name="x", metric=["a"], entity=["b"], frequency="Quarterly")
    )
    assert "1 metric" in spec
    assert "cartesian" in spec


def test_query_groups_overlay_same_metric_units():
    from textual_plotext import PlotextPlot

    from jstdata.actions.query_view import (
        group_plot_records,
        plot_group_label,
        plot_history,
    )

    def rec(sid, metric, freq, units, entity, values):
        obs = [
            {
                "observation_timestamp": f"{2020 + i}-01-01T00:00:00",
                "release_timestamp": f"{2020 + i}-06-01T00:00:00",
                "value": val,
                "release_id": f"r{i}",
            }
            for i, val in enumerate(values)
        ]
        return {
            "id": sid,
            "label": f"Household debt to GDP for {entity}",
            "metric_id": metric,
            "entities": [{"id": entity.lower(), "label": entity}],
            "frequency": freq,
            "units": units,
            "source": "FSI",
            "n_observations": len(obs),
            "last": obs[-1],
            "observations": obs,
        }

    france = rec("s-fr", "hhd", "Annual", "Ratio", "France", [70.1, 71.5])
    united = rec(
        "s-us", "hhd", "Quarterly", "Ratio", "United States", [58.0, 59.2]
    )
    other = rec("s-cpi", "cpi", "Quarterly", "Index", "France", [100.0, 102.0])
    groups = group_plot_records([france, united, other])
    keys = [
        (g.metric_id, g.units, len(g.records)) for g in groups
    ]
    assert ("hhd", "Ratio", 2) in keys
    assert ("cpi", "Index", 1) in keys
    overlay = next(g for g in groups if len(g.records) == 2)
    assert "Household debt to GDP" in plot_group_label(overlay)
    assert "Ratio" in plot_group_label(overlay)
    plt = PlotextPlot().plt
    assert plot_history(plt, overlay.records) is True
    plt.plotsize(60, 14)
    chart = plt.uncolorize(plt.build())
    assert "France" in chart
    assert "United States" in chart
    lonely = group_plot_records(
        [
            rec("a", "", "Annual", "X", "France", [1.0]),
            rec("b", "", "Annual", "X", "Spain", [2.0]),
        ]
    )
    assert len(lonely) == 2


def test_query_chart_axis_export_and_cursor():
    from datetime import datetime

    from textual_plotext import PlotextPlot

    from jstdata.actions.query_view import (
        calendar_xticks,
        coverage_status,
        coverage_years,
        cursor_dates,
        observation_on_date,
        plot_history,
        query_python_snippet,
        records_to_csv,
    )
    from jstdata.investigation.model import Query

    obs = []
    for year in range(2019, 2025):
        obs.append(
            {
                "observation_timestamp": f"{year}-01-01T00:00:00",
                "release_timestamp": f"{year}-08-01T00:00:00",
                "value": 70 + year - 2019,
                "release_id": f"r{year}",
            }
        )
    record = {
        "id": "s-us",
        "label": "Household debt to GDP for United States",
        "metric_id": "household-debt-to-gdp",
        "entities": [{"id": "united-states", "label": "United States"}],
        "frequency": "Quarterly",
        "units": "Ratio",
        "source": "FSI",
        "n_observations": len(obs),
        "last": obs[-1],
        "observations": obs,
    }
    ticks, labels = calendar_xticks([record])
    assert labels == ["2019", "2020", "2021", "2022", "2023", "2024"]
    assert ticks[0] == "2019-01-01"
    assert coverage_years([record]) == "2019–2024"
    dates = cursor_dates([record])
    assert dates[0].year == 2019
    assert observation_on_date(record, datetime(2021, 1, 1))["value"] == 72
    assert observation_on_date(record, datetime(2021, 4, 1)) is None
    plt = PlotextPlot().plt
    assert plot_history(plt, record, cursor_date=datetime(2021, 1, 1)) is True
    plt.plotsize(60, 14)
    chart = plt.uncolorize(plt.build())
    assert "2019" in chart
    assert "2024" in chart
    assert "2019-03-31" not in chart
    assert "%" not in chart
    csv_text = records_to_csv([record])
    assert "date,series_id,entity,metric_id,value" in csv_text
    assert "s-us,united-states,household-debt-to-gdp,75" in csv_text
    snippet = query_python_snippet(
        Query(id=2, metric=["household-debt-to-gdp"], entity=["france", "united-states"])
    )
    assert "client.query_df" in snippet
    assert "household-debt-to-gdp" in snippet
    status = coverage_status(
        Query(id=2, metric=["household-debt-to-gdp"], entity=["france", "united-states"]),
        [record],
    )
    assert "missing" in status
    assert "france" in status


def test_series_to_record_is_json_not_frame():
    from datetime import datetime

    from jstdata.actions.query_view import series_to_record
    from jstdata.models import Entity, Observation, Series, TimeSeries

    ts = TimeSeries(
        series=Series(
            id="s1",
            label="GDP France",
            frequency="Annual",
            source="OECD",
            units="USD",
            seasonal_adjustment="NSA",
            last_updated=datetime(2024, 1, 1),
            metric_id="gdp",
            entities=[Entity(id="france", label="France")],
        ),
        observations=[
            Observation(
                series_id="s1",
                observation_timestamp=datetime(2023, 1, 1),
                release_timestamp=datetime(2024, 1, 1),
                value=1.2,
                release_id="r1",
            )
        ],
    )
    record = series_to_record(ts)
    assert record["id"] == "s1"
    assert record["metric_id"] == "gdp"
    assert record["entities"] == [{"id": "france", "label": "France"}]
    assert record["n_observations"] == 1
    assert record["last"]["value"] == 1.2
    assert "observations" in record
    import json

    json.dumps(record)


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

    def create_in_tmp(investigation_id=None, *, title=None, notes=None, app_dir=None, **kwargs):
        return api_mod.create_investigation(
            investigation_id,
            title=title,
            notes=notes,
            app_dir=tmp_path,
            activate=True,
        )

    def open_in_tmp(investigation_id, *, app_dir=None, **kwargs):
        return api_mod.open_investigation(investigation_id, app_dir=tmp_path)

    monkeypatch.setattr(inv_pkg, "create_investigation", create_in_tmp)
    monkeypatch.setattr(inv_pkg, "open_investigation", open_in_tmp)
    monkeypatch.setattr(inv_pkg, "run_investigation_host", fake_run)

    runner = CliRunner()
    result = runner.invoke(cli, ["new", "--id", "alpha", "--title", "Alpha"])
    assert result.exit_code == 0, result.output
    assert "Created investigation 'alpha'" in result.output
    assert launched == ["run"]
    assert (tmp_path / "investigations" / "alpha.yaml").is_file()
    assert load_active(app_dir=tmp_path).investigation == "alpha"

    launched.clear()
    result = runner.invoke(cli, ["open", "alpha"])
    assert result.exit_code == 0, result.output
    assert "Opened investigation 'alpha'" in result.output
    assert launched == ["run"]

    result = runner.invoke(cli, ["open", "missing"])
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


def test_query_inspect_cartesian_coverage():
    class FakePreview:
        def __init__(self, metric_id, entity_id):
            self.metric_id = metric_id
            self.entity_id = entity_id
            self.series_count = 1
            self.observation_count = 10
            self.earliest = None
            self.latest = None

    class FakeClient:
        def preview(self, metric, entity, limit=50, offset=0):
            return [FakePreview("gdp", "france")]

    query = Query(id=1, metric=["gdp"], entity=["france", "usa"])
    payload = query.inspect(FakeClient())
    assert payload["cartesian"] is True
    assert payload["counts"]["expected_pairs"] == 2
    assert payload["counts"]["resolved"] == 1
    assert payload["missing"] == [{"metric": "gdp", "entity": "usa"}]
