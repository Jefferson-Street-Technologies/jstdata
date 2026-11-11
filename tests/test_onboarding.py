"""Tests for coached tutorials (catalog, coach, state, and host)."""

import asyncio

import pytest

from jstdata.onboarding import (
    LEVELS,
    CoachSnapshot,
    TutorialCoach,
    TutorialPhase,
    completed_tutorials,
    list_tutorials,
    load_tutorial,
    mark_completed,
    mark_prompted,
    should_prompt,
)


def test_bundled_tutorials_cover_every_level():
    tutorials = list_tutorials()
    assert [t.id for t in tutorials] == [
        "big-tech-gross-profit",
        "gulf-oil-exports",
        "gulf-fiscal-breakeven",
    ]
    assert [t.level for t in tutorials] == list(LEVELS)


@pytest.mark.parametrize("tutorial", list_tutorials(), ids=lambda t: t.id)
def test_every_tutorial_builds_a_valid_investigation(tutorial):
    inv = tutorial.build_investigation()
    assert inv.id == f"tutorial-{tutorial.id}"
    assert inv.workspace.is_empty()  # every run starts fresh
    assert tutorial.start_action in inv.actions
    for phase in tutorial.phases:
        if "active_action" in phase.until:
            assert int(phase.until["active_action"]) in inv.actions
    assert tutorial.phases[-1].is_done
    if tutorial.level != "open":  # open level deliberately leaves keys to you
        assert all(p.do for p in tutorial.phases), tutorial.id


def test_tutorials_start_like_jst_new():
    """Tutorials teach the native flow: a bare investigation on Home."""
    for tutorial in list_tutorials():
        inv = tutorial.build_investigation()
        assert [a.type for a in inv.actions.values()] == ["home"]
        assert tutorial.start_action == 0


def test_gulf_tutorial_is_metric_first():
    phases = load_tutorial("gulf-oil-exports").phases
    assert phases[0].until == {"workspace_has_metric": "crude-oil-exports"}
    # Countries come from the metric's own view, after the metrics.
    assert phases[2].until == {"screen": "MetricScreen", "screen_flag": "loaded"}
    assert phases[3].until == {"workspace_entity_count": 5}


def test_unknown_until_key_rejected():
    with pytest.raises(ValueError, match="Unknown 'until'"):
        TutorialPhase.from_dict({"text": "x", "until": {"session_entity_count": 1}})


def test_phase_conditions_are_anded():
    phase = TutorialPhase.from_dict(
        {"text": "x", "until": {"active_action": 2, "focus": "rank-list"}}
    )
    assert not phase.satisfied(CoachSnapshot(active_action=2))
    assert not phase.satisfied(CoachSnapshot(focus_ids=frozenset({"rank-list"})))
    assert phase.satisfied(
        CoachSnapshot(active_action=2, focus_ids=frozenset({"rank-list"}))
    )


def test_coach_walks_gulf_tutorial():
    coach = TutorialCoach(load_tutorial("gulf-oil-exports"))
    assert not coach.advance(CoachSnapshot(screen="HomeScreen"))
    metrics = ("crude-oil-exports", "crude-oil-production")
    assert coach.advance(CoachSnapshot(metrics=metrics))
    assert coach.phase_index == 2  # skipped past both metric phases at once
    assert not coach.advance(CoachSnapshot(metrics=metrics, screen="MetricScreen"))
    snap = CoachSnapshot(
        metrics=metrics, screen="MetricScreen", screen_flags=frozenset({"loaded"})
    )
    assert coach.advance(snap)
    gulf = ("saudi-arabia", "iraq", "kuwait", "qatar", "oman")
    coach.advance(CoachSnapshot(metrics=metrics, entities=gulf, query_count=1))
    assert coach.current_phase.until == {"screen_flag": "chart"}
    coach.advance(
        CoachSnapshot(
            metrics=metrics,
            entities=gulf,
            query_count=1,
            screen_flags=frozenset({"loaded", "chart"}),
        )
    )
    assert coach.is_finished


def test_render_hints_follow_focus_and_progress():
    gulf = TutorialCoach(load_tutorial("gulf-oil-exports"), phase_index=3)
    _, _, do = gulf.render(CoachSnapshot(entities=("saudi-arabia",)))
    assert do.startswith("▶ DO: /")
    assert "collected so far: 1/5 entities" in do
    coach = TutorialCoach(load_tutorial("big-tech-gross-profit"))
    coach.phase_index = 5  # "g → 4" (needs navigation)
    _, _, do = coach.render(CoachSnapshot(focus_in_input=True))
    assert "Esc" in do and "text box" in do
    assert "[" not in do  # would be parsed as Textual markup


def test_state_round_trip(tmp_path):
    assert should_prompt(tmp_path)
    mark_prompted(tmp_path)
    assert not should_prompt(tmp_path)
    mark_completed(tmp_path, "gulf-oil-exports")
    assert completed_tutorials(tmp_path) == {"gulf-oil-exports"}
    (tmp_path / "onboarding.json").write_text("not json")
    assert should_prompt(tmp_path)  # corrupt state degrades to defaults


# --- Host --------------------------------------------------------------


class _EmptyClient:
    """Enough of JSTDataClient for screens to mount: every call returns []."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: []


def _host(tmp_path, **kwargs):
    from jstdata.investigation import resume_or_create_investigation
    from jstdata.investigation.host import InvestigationHost

    mine = resume_or_create_investigation(app_dir=tmp_path)
    return mine, InvestigationHost(_EmptyClient(), app_dir=tmp_path, **kwargs)


def test_first_run_opens_tutorial_menu_once(tmp_path):
    from jstdata.onboarding.ui import TutorialMenu

    async def run():
        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, TutorialMenu)
            assert app.screen.first_run
            await pilot.press("escape")
            await pilot.pause()
            assert app.coach is None

        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not isinstance(app.screen, TutorialMenu)

    asyncio.run(run())


def test_start_and_leave_tutorial_restores_investigation(tmp_path):
    mark_prompted(tmp_path)

    async def run():
        mine, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("j", "enter")  # second row: gulf-oil-exports
            await pilot.pause(0.5)
            assert app.active.investigation == "tutorial-gulf-oil-exports"
            assert app.active.action == 0  # Home, like jst new
            assert app.workspace.is_empty()
            assert app._coach_bar is not None and app._coach_bar.is_attached

            # Coach advances from real workspace state.
            app.workspace.add_metric("crude-oil-exports")
            await pilot.pause(0.4)
            assert app.coach.phase_index == 1

            await pilot.press("t")
            await pilot.pause()
            await pilot.press("enter")  # first row: leave
            await pilot.pause(0.5)
            assert app.active.investigation == mine.id
            assert app.coach is None
            assert app._coach_bar is None

    asyncio.run(run())


def test_tutorial_command_in_palette(tmp_path):
    from jstdata.onboarding.ui import TutorialMenu

    mark_prompted(tmp_path)

    async def run():
        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("colon")
            await pilot.pause()
            await pilot.press(*"tutorial", "enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, TutorialMenu)

    asyncio.run(run())


def test_opening_another_investigation_ends_tutorial(tmp_path):
    mark_prompted(tmp_path)

    async def run():
        mine, app = _host(tmp_path, start_tutorial="big-tech-gross-profit")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert app.coach is not None
            app._switch_investigation(mine.id, 0)
            await pilot.pause(0.5)
            assert app.coach is None
            assert app._coach_bar is None

    asyncio.run(run())


def test_coach_follows_into_workspace_drawer(tmp_path):
    from jstdata.investigation.workspace_drawer import WorkspaceDrawer

    mark_prompted(tmp_path)

    async def run():
        _, app = _host(tmp_path, start_tutorial="big-tech-gross-profit")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            base = app.screen
            await pilot.press("colon")
            await pilot.pause()
            await pilot.press(*"workspace", "enter")
            await pilot.pause(0.5)
            assert isinstance(app.screen, WorkspaceDrawer)
            assert app._coach_screen is app.screen  # instructions stay visible
            await pilot.press("escape")
            await pilot.pause(0.5)
            assert app._coach_screen is base and app._coach_bar.is_attached

    asyncio.run(run())


def test_queries_destination_highlights_create_row(tmp_path):
    from textual.widgets import ListView

    mark_prompted(tmp_path)

    async def run():
        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.workspace.add_metric("gdp")
            app.workspace.add_entity("france")
            await pilot.press("5")
            await pilot.pause()
            assert app.screen.query_one("#home-list", ListView).index == 0
            await pilot.press("enter")  # "Create from workspace"
            await pilot.pause(0.5)
            assert len(app.investigation.queries) == 1

    asyncio.run(run())
