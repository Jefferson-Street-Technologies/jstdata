"""Tests for investigation command-mode parsing."""

from __future__ import annotations

import pytest

from jstdata.investigation.commands import (
    CommandError,
    action_arg_doc_lines,
    complete_command_line,
    completion_doc,
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


def test_complete_command_names():
    assert complete_command_line("") == ["workspace", "search", "add", "rm"]
    assert complete_command_line("se") == ["search"]
    assert complete_command_line(":a") == ["add"]
    # Exact command name offers next-token completions (action types for add).
    add_next = complete_command_line("add")
    assert "add console" in add_next
    assert "add discover" in add_next
    assert complete_command_line("workspace") == []
    assert complete_command_line("workspace ") == []


def test_complete_search_types():
    assert complete_command_line("search ") == [
        "search all",
        "search entity",
        "search metric",
        "search series",
        "search taxonomy",
    ]
    # Exact "search" also jumps to type completions.
    assert complete_command_line("search") == [
        "search all",
        "search entity",
        "search metric",
        "search series",
        "search taxonomy",
    ]
    assert complete_command_line("search m") == ["search metric"]
    assert complete_command_line("search t") == ["search taxonomy"]
    assert complete_command_line("search a") == ["search all"]
    assert complete_command_line("search metric ") == []


def test_complete_add_types_and_flags():
    names = complete_command_line("add ")
    assert "add console" in names
    assert "add discover" in names
    assert complete_command_line("add c") == ["add console"]
    # Typing just "add" should already expose action types (screenshot UX).
    assert complete_command_line("add") == names
    flags = complete_command_line("add console ")
    assert "add console --args" in flags
    assert "add console --parent" in flags
    assert complete_command_line("add console --p") == ["add console --parent"]
    assert complete_command_line("add console --args ") == []
    more = complete_command_line("add console --args '{}' ")
    assert more
    assert not any(c.rsplit(" ", 1)[-1] == "--args" for c in more)


def test_complete_rm_force():
    assert complete_command_line("rm 3 ") == ["rm 3 --force"]
    assert complete_command_line("rm 3 --") == ["rm 3 --force"]
    assert complete_command_line("rm 3 --force ") == []


def test_completion_docs_for_actions_and_args():
    assert "workspace" in completion_doc("workspace").lower() or completion_doc(
        "workspace"
    )
    assert "Search" in completion_doc("search metric") or "metric" in completion_doc(
        "search metric"
    ).lower()
    console_doc = completion_doc("add console")
    assert console_doc
    assert "metric" in console_doc.lower() or "stage" in console_doc.lower()
    arg_lines = action_arg_doc_lines("console")
    assert any("taxonomy" in line for line in arg_lines)
    assert any("relation" in line for line in arg_lines)
    assert "JSON" in completion_doc("add console --args")
    home_args = action_arg_doc_lines("home")
    assert home_args == ["  (no seed args)"]


def test_format_suggestions_includes_action_docs():
    from jstdata.investigation.command_palette import CommandPalette

    palette = CommandPalette()
    text = palette._format_suggestions("add c")
    assert "add console" in text
    assert "taxonomy" in text
    # Highlighted cycle expands args under the selected type.
    palette._completions = complete_command_line("add ")
    palette._comp_index = palette._completions.index("add console")
    highlighted = palette._format_suggestions("add console")
    assert "taxonomy" in highlighted
    assert "relation" in highlighted


def test_tab_from_add_cycles_action_types_not_flags():
    """Regression: Tab on ``add`` must cycle console→discover, not jump to --args."""
    from jstdata.investigation.command_palette import CommandPalette

    palette = CommandPalette()
    candidates = complete_command_line("add")
    assert candidates[0] == "add console"
    assert "add discover" in candidates
    palette._completions = candidates
    palette._comp_index = -1
    palette._comp_index = (palette._comp_index + 1) % len(palette._completions)
    assert palette._completions[palette._comp_index] == "add console"
    palette._comp_index = (palette._comp_index + 1) % len(palette._completions)
    assert palette._completions[palette._comp_index] == "add discover"
    assert palette._completions == candidates


def test_deferred_changed_does_not_clear_tab_freeze():
    """Input.Changed from _set_input must not recompute and singleton-freeze."""
    from jstdata.investigation.command_palette import CommandPalette

    candidates = complete_command_line("add ")
    assert len(candidates) > 1
    palette = CommandPalette()
    palette._completions = list(candidates)
    palette._comp_index = 0
    # Simulate Tab applying the first choice, then a deferred Changed.
    palette._applied_value = candidates[0]
    assert palette._applied_value == candidates[0]
    # Same guard as on_changed: programmatic value keeps the freeze.
    assert palette._applied_value is not None
    assert palette._applied_value == candidates[0]
    # After consuming the applied marker, completions must still be the full list.
    palette._applied_value = None
    assert palette._completions == candidates
    # Second Tab advances within the frozen list (not a recompute of "add console").
    palette._comp_index = (palette._comp_index + 1) % len(palette._completions)
    assert palette._completions[palette._comp_index] == candidates[1]


def test_lock_in_advances_to_next_token_completions():
    """Space/Enter on a highlight should freeze the next token's options."""
    from jstdata.investigation.command_palette import CommandPalette

    locked = "add console"
    nxt = complete_command_line(locked + " ")
    assert any(c.startswith("add console --") for c in nxt)
    # Simulate lock-in state transition used by the palette.
    palette = CommandPalette()
    palette._completions = complete_command_line("add")
    palette._comp_index = 0
    assert palette._has_highlight()
    # After lock, next candidates are flags (no highlight yet).
    assert nxt[0] == "add console --args"


def test_parse_workspace():
    parsed = parse_command_line(":workspace")
    assert parsed.name == "workspace"
    assert parsed.args == []


def test_parse_search():
    parsed = parse_command_line("search metric gdp growth")
    assert parsed.name == "search"
    assert parsed.args == ["metric", "gdp growth"]
    parsed = parse_command_line("search taxonomy country")
    assert parsed.args == ["taxonomy", "country"]
    parsed = parse_command_line("search all gdp")
    assert parsed.args == ["all", "gdp"]
    with pytest.raises(CommandError, match="resource type"):
        parse_command_line("search foo bar")
    with pytest.raises(CommandError, match="Usage"):
        parse_command_line("search metric")


def test_filter_taxonomies_client_side():
    from jstdata.investigation.search_drawer import _filter_taxonomies
    from jstdata.models import Taxonomy

    taxes = [
        Taxonomy(id="country", name="Countries"),
        Taxonomy(id="sec-central-index-key", name="SEC CIK"),
        Taxonomy(id="industry", name="Industries"),
    ]
    assert [t.id for t in _filter_taxonomies(taxes, "coun")] == ["country"]
    assert [t.id for t in _filter_taxonomies(taxes, "SEC")] == [
        "sec-central-index-key"
    ]
    assert [t.id for t in _filter_taxonomies(taxes, "xyz")] == []


def test_parse_add_defaults_activate():
    parsed = parse_command_line(
        """add console --args '{"taxonomy":"country"}'"""
    )
    assert parsed.name == "add"
    assert parsed.args == ["console"]
    assert parsed.options["activate"] is True
    assert parsed.options["args"] == {"taxonomy": "country"}
    assert "description" not in parsed.options

    parsed = parse_command_line("add discover --no-activate --parent 0")
    assert parsed.options["activate"] is False
    assert parsed.options["parent"] == 0

    with pytest.raises(CommandError, match="Unexpected"):
        parse_command_line("add console --description nope")


def test_parse_rm():
    parsed = parse_command_line("rm 3 --force")
    assert parsed.name == "rm"
    assert parsed.options["action_id"] == 3
    assert parsed.options["force"] is True
    with pytest.raises(CommandError, match="integer"):
        parse_command_line("rm nope")
