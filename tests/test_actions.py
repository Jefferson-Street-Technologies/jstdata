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
