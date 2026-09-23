"""Tests for ``jst agent-guide``."""

from click.testing import CliRunner

from jstdata.agent_guide import (
    _SESSION_FIELDS_OMIT,
    format_command_params,
    iter_leaf_commands,
    package_version,
    render_agent_guide,
)
from jstdata.cli import cli
from jstdata.workflows import list_steps


def test_agent_guide_cli():
    runner = CliRunner()
    result = runner.invoke(cli, ["agent-guide"])
    assert result.exit_code == 0
    out = result.output
    assert out.startswith("# jstdata agent guide (v")
    assert "## Hard rules" in out
    assert "## Default operating policy" in out
    assert "## Choose an operating mode" in out
    assert "### Catalog discovery" in out
    assert "### Agent analysis" in out
    assert "### Human handoff" in out
    assert "## Data model" in out
    assert "## Resolution" in out
    assert "## Common recipes" in out
    assert "Rank GDP among EU countries" in out
    assert "session has entities" in out or "--session-entities=false" in out
    assert "## Composition and workflows" in out
    assert "## Sessions" in out
    assert "### Session JSON fields" in out
    assert "### Create and modify" in out
    assert "`Session.load" in out or "Session.load" in out
    assert "--session" in out
    assert "`metric`" in out
    assert "## CLI commands" in out
    assert "## Interactive steps" in out
    assert "`jst agent-guide`" in out
    assert "`jst metric search`" in out
    assert "`jst workflow create`" in out
    assert "`jst run`" in out
    assert "Never invent" in out
    assert "--format json" in out
    assert "Do not launch interactive TUIs" in out
    assert "Host chrome" not in out
    assert "Step keybindings" not in out


def test_agent_guide_omits_legacy_session_fields():
    guide = render_agent_guide(cli)
    for name in _SESSION_FIELDS_OMIT:
        assert f"`{name}`" not in guide, name
    assert "`tail`" in guide
    assert "`taxonomy`" in guide


def test_agent_guide_session_fields_match_primary_model():
    from dataclasses import fields

    from jstdata.session import Session

    guide = render_agent_guide(cli)
    for f in fields(Session):
        if f.name in _SESSION_FIELDS_OMIT:
            continue
        assert f"`{f.name}`" in guide, f.name


def test_agent_guide_lists_all_leaf_commands():
    guide = render_agent_guide(cli)
    for invocation, _cmd in iter_leaf_commands(cli):
        assert f"`{invocation}`" in guide, invocation


def test_agent_guide_lists_all_steps_without_keybindings():
    guide = render_agent_guide(cli)
    specs = list_steps()
    assert specs, "expected registered steps"
    for spec in specs:
        assert f"`{spec.id}`" in guide
        if spec.example:
            assert spec.example in guide
    assert "Step keybindings" not in guide


def test_agent_guide_policy_before_cli_table():
    guide = render_agent_guide(cli)
    assert guide.index("## Hard rules") < guide.index("## Common recipes")
    assert guide.index("## Common recipes") < guide.index("## CLI commands")
    assert guide.index("## Interactive steps") < guide.index("## CLI commands")


def test_format_command_params_includes_options():
    # Pick a known leaf with several options.
    cmd = None
    for invocation, leaf in iter_leaf_commands(cli):
        if invocation == "jst metric search":
            cmd = leaf
            break
    assert cmd is not None
    params = format_command_params(cmd)
    assert "QUERY" in params or "[QUERY]" in params
    assert "--taxonomy" in params
    assert "--format" in params


def test_package_version_nonempty():
    assert package_version()
    assert package_version() != ""
