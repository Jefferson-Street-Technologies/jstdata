"""Interactive project picker for ``jst ls``."""

from __future__ import annotations

from pathlib import Path

from jstdata.investigation import create_investigation, create_project_with_investigation
from jstdata.investigation.project_picker import (
    ProjectPickerApp,
    load_project_rows,
)


def test_load_project_rows_marks_active(tmp_path: Path):
    create_project_with_investigation(
        project_id="alpha",
        title="Alpha",
        investigation_id="alpha",
        app_dir=tmp_path,
    )
    create_project_with_investigation(
        project_id="beta",
        title="Beta Research",
        investigation_id="beta",
        app_dir=tmp_path,
    )
    rows = load_project_rows(app_dir=tmp_path, active_project="beta")
    assert [r.project_id for r in rows] == ["alpha", "beta"]
    assert rows[0].title == "Alpha"
    assert rows[1].active is True
    assert rows[0].active is False


def test_picker_enter_returns_project_id(tmp_path: Path):
    create_investigation("alpha", title="Alpha", app_dir=tmp_path)
    create_investigation("beta", title="Beta", app_dir=tmp_path)
    rows = load_project_rows(app_dir=tmp_path, active_project="alpha")
    app = ProjectPickerApp(rows, active_project="alpha")

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("j")  # move to beta
            await pilot.press("enter")

    import asyncio

    asyncio.run(run())
    assert app.return_value == "beta"


def test_picker_quit_returns_none(tmp_path: Path):
    create_investigation("alpha", title="Alpha", app_dir=tmp_path)
    rows = load_project_rows(app_dir=tmp_path)
    app = ProjectPickerApp(rows)

    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")

    import asyncio

    asyncio.run(run())
    assert app.return_value is None


def test_run_project_picker_empty(tmp_path: Path, monkeypatch):
    # Empty picker: quit immediately via binding without hanging the test.
    app = ProjectPickerApp([])
    async def run():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")

    import asyncio

    asyncio.run(run())
    assert app.return_value is None
    # load helper stays empty too
    assert load_project_rows(app_dir=tmp_path) == []
