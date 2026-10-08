"""Tests for the action catalog (formerly steps)."""

import pytest

from jstdata.investigation.model import Workspace
from jstdata.actions.base import (
    ActionArgument,
    ActionSpec,
    format_action_help,
    get_action,
    label_for,
    list_actions,
    parse_action_kwargs,
    resource_type_for_id,
)


def test_console_is_registered():
    import jstdata.actions  # noqa: F401

    ids = [s.id for s in list_actions()]
    assert "console" in ids
    console = get_action("console")
    assert console.name == "Console"
    assert "action add console" in console.to_dict()["example"]
    assert any(b["key"] == "/" for b in console.to_dict()["bindings"])
    assert any(a["name"] == "taxonomy" for a in console.to_dict()["arguments"])
    assert any(a["name"] == "relation" for a in console.to_dict()["arguments"])
    resource_type = next(
        a for a in console.to_dict()["arguments"] if a["name"] == "resource_type"
    )
    assert resource_type["flag"] == "--resource-type"
    assert resource_type["choices"] == ["series", "metric", "entity"]


def test_table_is_registered():
    import jstdata.actions  # noqa: F401

    table = get_action("table")
    assert table.name == "Table"
    assert any(b["key"] == "r" for b in table.to_dict()["bindings"])
    assert any(b["key"] == "enter" for b in table.to_dict()["bindings"])
    sort_by = next(a for a in table.to_dict()["arguments"] if a["name"] == "sort_by")
    assert sort_by["choices"] == ["id", "value"]


def test_metric_and_entity_views_registered():
    import jstdata.actions  # noqa: F401

    metric = get_action("metric")
    entity = get_action("entity")
    assert metric.name == "Metric"
    assert entity.name == "Entity"
    ids = {s.id for s in list_actions()}
    assert {"metric", "entity", "table"} <= ids


def test_metrics_view_registered_with_mode_arg():
    import jstdata.actions  # noqa: F401

    metrics = get_action("metrics")
    assert metrics.name == "Metrics"
    mode = next(a for a in metrics.to_dict()["arguments"] if a["name"] == "mode")
    assert mode["choices"] == ["union", "intersect"]
    assert mode["default"] == "union"
    assert any(b["key"] == "m" for b in metrics.to_dict()["bindings"])


def test_metrics_view_pagination_state():
    from unittest.mock import MagicMock

    from jstdata.actions.metrics_view import PAGE_SIZE, MetricsScreen, create_metrics_screen
    from jstdata.actions.pagination import page_fetch_limit, take_page
    from jstdata.investigation.model import ResearchContext, Workspace

    screen = create_metrics_screen(
        MagicMock(),
        Workspace(entity=["e1"]),
        input_context=ResearchContext(entity=["e1"]),
        mode="union",
    )
    assert isinstance(screen, MetricsScreen)
    assert screen.page_offset == 0
    assert screen.has_more is False
    assert PAGE_SIZE == 20
    page, more = take_page(list(range(page_fetch_limit(PAGE_SIZE))), PAGE_SIZE)
    assert len(page) == PAGE_SIZE
    assert more is True


def test_table_drill_options_follow_focus_mode():
    from jstdata.actions.table import TableRow, TableScreen
    from jstdata.investigation.model import ResearchContext, Workspace
    from unittest.mock import MagicMock

    screen = TableScreen(MagicMock(), Workspace(), input_context=ResearchContext())
    screen._parent_overrides = None
    screen._metric_ids = ["assets", "cash"]
    screen._col_index = 1
    screen._rows = [TableRow(entity_id="cik:1", entity_label="Alphabet")]
    screen._loaded = True

    screen._focus_mode = "column"
    col_opts = screen._drill_options()
    assert len(col_opts) == 1
    assert col_opts[0].action_type == "metric"
    assert col_opts[0].context.metric == ["cash"]

    # Row mode needs a ListView highlight — test the entity builder directly.
    ent = screen._entity_drill_option(screen._rows[0])
    assert ent.action_type == "entity"
    assert ent.context.entity == ["cik:1"]


def test_table_short_headers():
    from jstdata.actions.table import TableScreen, COL_WIDTH
    from jstdata.investigation.model import ResearchContext, Workspace
    from unittest.mock import MagicMock

    screen = TableScreen(MagicMock(), Workspace(), input_context=ResearchContext())
    short = screen._short_metric_header("long-term-debt")
    assert len(short) == COL_WIDTH
    assert "long-term-debt" not in short.strip() or len("long-term-debt") <= COL_WIDTH
    assert short.strip() == "long-ter"


def test_format_action_help_console():
    text = format_action_help(get_action("console"))
    assert "Console" in text
    assert "jst action add console" in text
    assert "--taxonomy" in text
    assert "--relation" in text
    assert "--resource-type" in text
    assert "Keybindings:" in text
    assert "/" in text


def test_unknown_step_raises():
    with pytest.raises(KeyError, match="Unknown action"):
        get_action("nope")


def test_parse_action_kwargs():
    spec = ActionSpec(
        id="demo",
        name="Demo",
        description="d",
        create_screen=lambda *a, **k: None,
        arguments=(
            ActionArgument(
                name="industry",
                type="string",
                description="Industry filter",
            ),
            ActionArgument(
                name="limit",
                type="integer",
                description="Max results",
                default=10,
            ),
        ),
    )
    assert parse_action_kwargs(spec, ["--industry", "semiconductors"]) == {
        "industry": "semiconductors",
        "limit": 10,
    }
    assert parse_action_kwargs(spec, ["--industry=chips", "--limit", "3"]) == {
        "industry": "chips",
        "limit": 3,
    }
    with pytest.raises(ValueError, match="Unknown option"):
        parse_action_kwargs(spec, ["--nope"])
    with pytest.raises(ValueError, match="integer"):
        parse_action_kwargs(spec, ["--limit", "x"])


def test_resource_type_and_label_helpers():
    workspace = Workspace(metric=["gdp"], entity=["usa"], series=["s1"])
    assert resource_type_for_id(workspace, "gdp") == "metric"
    assert resource_type_for_id(workspace, "usa") == "entity"
    assert resource_type_for_id(workspace, "s1") == "series"
    assert label_for({"gdp": "GDP"}, "gdp") == "GDP"
    assert label_for({}, "gdp") == "gdp"


def test_console_rejects_unknown_flags():
    with pytest.raises(ValueError, match="Unknown option"):
        parse_action_kwargs(get_action("console"), ["--industry", "x"])
