"""Tests for investigation command-mode parsing."""

from __future__ import annotations

import pytest

from jstdata.investigation.commands import (
    CommandError,
    list_command_suggestions,
    parse_command_line,
)


def test_list_suggestions_filters_prefix():
    names = [c.name for c in list_command_suggestions("se")]
    assert names == ["search"]
    assert {c.name for c in list_command_suggestions()} >= {
        "workspace",
        "search",
        "add",
        "rm",
    }


def test_parse_workspace():
    parsed = parse_command_line(":workspace")
    assert parsed.name == "workspace"
    assert parsed.args == []


def test_parse_search():
    parsed = parse_command_line("search metric gdp growth")
    assert parsed.name == "search"
    assert parsed.args == ["metric", "gdp growth"]
    with pytest.raises(CommandError, match="resource type"):
        parse_command_line("search foo bar")
    with pytest.raises(CommandError, match="Usage"):
        parse_command_line("search metric")


def test_parse_add_defaults_activate():
    parsed = parse_command_line(
        """add console --args '{"taxonomy":"country"}' --description browse"""
    )
    assert parsed.name == "add"
    assert parsed.args == ["console"]
    assert parsed.options["activate"] is True
    assert parsed.options["args"] == {"taxonomy": "country"}
    assert parsed.options["description"] == "browse"

    parsed = parse_command_line("add discover --no-activate --parent 0")
    assert parsed.options["activate"] is False
    assert parsed.options["parent"] == 0


def test_parse_rm():
    parsed = parse_command_line("rm 3 --force")
    assert parsed.name == "rm"
    assert parsed.options["action_id"] == 3
    assert parsed.options["force"] is True
    with pytest.raises(CommandError, match="integer"):
        parse_command_line("rm nope")
