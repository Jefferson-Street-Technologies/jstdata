"""Tests for ``jst agent guide``."""

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
    result = runner.invoke(cli, ["agent", "guide"])
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
    assert "## Audited research workflow" in out
    assert "## Common recipes" in out
    assert "Resolve a company and its peers" in out
    assert "Do not edit project / investigation YAML" in out
    assert "typically edit" not in out
    assert "`jst agent install`" in out
    assert "`jst agent guide`" in out
    assert "workspace_entities=false" in out or "workspace_metrics=false" in out
    assert "## Projects, investigations, and curation" in out
    assert "## Workspaces" in out
    assert "### Workspace resource fields" in out
    assert "`metric`" in out
    assert "Input Context" in out
    assert "`table`" in out
    assert "`metrics`" in out
    assert "`release`" in out
    assert "plain-bagel" in out
    assert "## CLI commands" in out
    assert "## TUI views (action catalog)" in out
    assert "`jst actions search metrics`" in out
    assert "`jst actions promote`" in out
    assert "`jst new`" in out
    assert "`jst open`" in out
    assert "`jst preview`" in out
    assert "`jst actions`" in out
    assert "Never invent" in out
    assert "--format json" in out
    assert "Do not launch interactive TUIs" in out
    assert "Tag your actor" in out
    assert "--actor agent" in out
    assert "--actor-filter agent" in out
    assert "`jst actions query patch`" in out
    assert "`jst actions remark add`" in out
    assert "--name Agent" in out
    assert "Name your remarks" in out
    assert "`jst actions workspace add`" in out
    assert "Shift+L" in out
    assert "Host chrome" not in out
    assert "Step keybindings" not in out
    # Retired groups must not be recommended as the agent workflow.
    assert "`jst action add`" not in out
    assert "`jst query add`" not in out
    assert "`jst query ls`" not in out


def test_agent_guide_hyphenated_is_deprecated():
    runner = CliRunner()
    result = runner.invoke(cli, ["agent-guide"])
    assert result.exit_code == 0
    assert "Deprecated" in result.output
    assert "jst agent guide" in result.output
    # Guide body still printed (soft deprecation).
    assert "# jstdata agent guide (v" in result.output


def test_agent_guide_omits_legacy_workspace_fields():
    guide = render_agent_guide(cli)
    # Only the workspace field table should omit these; the word may appear elsewhere.
    start = guide.index("### Workspace resource fields")
    end = guide.index("## TUI views (action catalog)")
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
    assert guide.index("## Audited research workflow") < guide.index("## Common recipes")
    assert guide.index("## Common recipes") < guide.index("## CLI commands")
    assert guide.index("## TUI views (action catalog)") < guide.index("## CLI commands")


def test_format_command_params_includes_options():
    cmd = None
    for invocation, leaf in iter_leaf_commands(cli):
        if invocation == "jst actions search metrics":
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
