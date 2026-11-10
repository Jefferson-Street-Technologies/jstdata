"""Copy the packaged jst agent skill into user-level skill directories."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

SKILL_NAME = "jst"
SKILL_FILENAME = "SKILL.md"

INSTALL_TARGETS: dict[str, tuple[str, ...]] = {
    "codex": (".agents", "skills", SKILL_NAME),
    "claude": (".claude", "skills", SKILL_NAME),
    "cursor": (".cursor", "skills", SKILL_NAME),
}

ALL_TARGETS = tuple(INSTALL_TARGETS)


def bundled_skill_text() -> str:
    return (
        files("jstdata.agent")
        .joinpath("skills", "jst", SKILL_FILENAME)
        .read_text(encoding="utf-8")
    )


def skill_dir(target: str, *, home: Path | None = None) -> Path:
    key = target.lower()
    if key not in INSTALL_TARGETS:
        raise ValueError(f"unknown agent install target: {target}")
    root = home or Path.home()
    return root.joinpath(*INSTALL_TARGETS[key])


def resolve_targets(target: str) -> tuple[str, ...]:
    key = target.lower()
    if key == "all":
        return ALL_TARGETS
    if key not in INSTALL_TARGETS:
        raise ValueError(f"unknown agent install target: {target}")
    return (key,)


def install_skill(target: str = "all", *, home: Path | None = None) -> list[Path]:
    """Write SKILL.md into the chosen user-level skill directories. Overwrites."""
    text = bundled_skill_text()
    written: list[Path] = []
    for name in resolve_targets(target):
        dest_dir = skill_dir(name, home=home)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / SKILL_FILENAME
        dest.write_text(text, encoding="utf-8")
        written.append(dest)
    return written
