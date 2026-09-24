"""Tests for ``jst agent-guide``."""

from click.testing import CliRunner

from jstdata.agent_guide import (
    _WORKSPACE_FIELDS_OMIT,
    format_command_params,
    iter_leaf_commands,
    package_version,
    render_agent_guide,
)
from jstdata.cli import cli
from jstdata.actions import list_actions


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
    assert "workspace_entities=false" in out or "workspace_metrics=false" in out
    assert "## Investigations and actions" in out
    assert "## Workspaces" in out
    assert "### Workspace resource fields" in out
    assert "`metric`" in out
    assert "## CLI commands" in out
    assert "## Actions" in out
    assert "`jst agent-guide`" in out
    assert "`jst api metric search`" in out
    assert "`jst investigation create`" in out
    assert "`jst action add`" in out
    assert "jst run" not in out
    assert "jst workflow" not in out
    assert "Never invent" in out
    assert "--format json" in out
    assert "Do not launch interactive TUIs" in out
    assert "Host chrome" not in out
    assert "Step keybindings" not in out


def test_agent_guide_omits_legacy_workspace_fields():
    guide = render_agent_guide(cli)
    # Only the workspace field table should omit these; the word may appear elsewhere.
    start = guide.index("### Workspace resource fields")
    end = guide.index("## Actions")
    table = guide[start:end]
    for name in _WORKSPACE_FIELDS_OMIT:
        assert f"| `{name}` |" not in table, name
    assert "`tail`" in table
    assert "`taxonomy`" in table


def test_agent_guide_workspace_fields_match_primary_model():
    from dataclasses import fields

    from jstdata.investigation.model import Workspace

    guide = render_agent_guide(cli)
    for f in fields(Workspace):
        if f.name in _WORKSPACE_FIELDS_OMIT:
            continue
        assert f"`{f.name}`" in guide, f.name


def test_agent_guide_lists_all_leaf_commands():
    guide = render_agent_guide(cli)
    for invocation, _cmd in iter_leaf_commands(cli):
        assert f"`{invocation}`" in guide, invocation


def test_agent_guide_lists_all_actions_without_keybindings():
    guide = render_agent_guide(cli)
    specs = list_actions()
    assert specs, "expected registered actions"
    for spec in specs:
        assert f"`{spec.id}`" in guide
        if spec.example:
            assert spec.example in guide
    assert "Step keybindings" not in guide


def test_agent_guide_policy_before_cli_table():
    guide = render_agent_guide(cli)
    assert guide.index("## Hard rules") < guide.index("## Common recipes")
    assert guide.index("## Common recipes") < guide.index("## CLI commands")
    assert guide.index("## Actions") < guide.index("## CLI commands")


def test_format_command_params_includes_options():
    cmd = None
    for invocation, leaf in iter_leaf_commands(cli):
        if invocation == "jst api metric search":
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
