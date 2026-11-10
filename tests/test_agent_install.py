"""Tests for the packaged jst skill and ``jst agent install``."""

from pathlib import Path

from click.testing import CliRunner

from jstdata.agent.install import (
    ALL_TARGETS,
    bundled_skill_text,
    install_skill,
    skill_dir,
)
from jstdata.cli import cli


def test_bundled_skill_is_a_pointer():
    text = bundled_skill_text()
    assert text.startswith("---\n")
    assert "name: jst" in text
    assert "jst agent-guide" in text
    assert "Do not edit investigation YAML" in text
    assert "jst query add" in text


def test_install_skill_cursor_only(tmp_path: Path):
    written = install_skill("cursor", home=tmp_path)
    dest = tmp_path / ".cursor" / "skills" / "jst" / "SKILL.md"
    assert written == [dest]
    assert dest.read_text(encoding="utf-8") == bundled_skill_text()
    assert not (tmp_path / ".claude" / "skills" / "jst" / "SKILL.md").exists()
    assert not (tmp_path / ".agents" / "skills" / "jst" / "SKILL.md").exists()


def test_install_skill_all_overwrites(tmp_path: Path):
    first = install_skill("all", home=tmp_path)
    assert {p.parent.name for p in first} == {"jst"}
    assert len(first) == len(ALL_TARGETS)
    for name in ALL_TARGETS:
        dest = skill_dir(name, home=tmp_path) / "SKILL.md"
        dest.write_text("stale\n", encoding="utf-8")
    second = install_skill("all", home=tmp_path)
    expected = bundled_skill_text()
    for path in second:
        assert path.read_text(encoding="utf-8") == expected


def _patch_install_home(monkeypatch, tmp_path: Path):
    def fake_install(target: str, *, home=None):
        return install_skill(target, home=tmp_path)

    monkeypatch.setattr("jstdata.agent.install.install_skill", fake_install)


def test_agent_install_cli(tmp_path: Path, monkeypatch):
    _patch_install_home(monkeypatch, tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["agent", "install", "claude"])
    assert result.exit_code == 0, result.output
    dest = tmp_path / ".claude" / "skills" / "jst" / "SKILL.md"
    assert dest.exists()
    assert str(dest) in result.output
    assert "Use the jst skill" in result.output
    assert "jst agent-guide" in result.output


def test_agent_install_cli_defaults_to_all(tmp_path: Path, monkeypatch):
    _patch_install_home(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli, ["agent", "install"])
    assert result.exit_code == 0, result.output
    for rel in (
        Path(".cursor") / "skills" / "jst" / "SKILL.md",
        Path(".claude") / "skills" / "jst" / "SKILL.md",
        Path(".agents") / "skills" / "jst" / "SKILL.md",
    ):
        assert (tmp_path / rel).exists(), rel


def test_agent_guide_nested_alias():
    runner = CliRunner()
    nested = runner.invoke(cli, ["agent", "guide"])
    top = runner.invoke(cli, ["agent-guide"])
    assert nested.exit_code == 0, nested.output
    assert nested.output == top.output
