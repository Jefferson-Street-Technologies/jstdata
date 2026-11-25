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
        "g7-government-debt",
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


# --- Tips --------------------------------------------------------------


def test_tip_for_follows_screen_destination_and_focus():
    from jstdata.onboarding import tip_for

    assert 'Press "/" to search' in tip_for(CoachSnapshot(screen="HomeScreen"))
    assert 'Press "Space" to collect' in tip_for(
        CoachSnapshot(screen="HomeScreen", home_dest="search")
    )
    assert "Chart, Table, Sources" in tip_for(CoachSnapshot(screen="QueryScreen"))
    assert '"Esc" to leave the box' in tip_for(
        CoachSnapshot(screen="QueryScreen", focus_in_input=True)
    )
    assert 'Press "/" to search' in tip_for(CoachSnapshot(screen="SomethingNew"))
    for text in (
        tip_for(CoachSnapshot(screen=s, home_dest=d))
        for s, d in [("HomeScreen", None), ("MetricScreen", None), ("TableScreen", None)]
    ):
        assert "[" not in text  # Textual markup


def test_tips_setting_round_trip(tmp_path):
    from jstdata.onboarding import set_tips_enabled, tips_enabled

    assert not tips_enabled(tmp_path)  # opt-in by default
    set_tips_enabled(tmp_path, True)
    assert tips_enabled(tmp_path)
    set_tips_enabled(tmp_path, False)
    assert not tips_enabled(tmp_path)
    mark_completed(tmp_path, "x")  # other state keeps the setting
    assert not tips_enabled(tmp_path)


def test_tip_bar_shows_and_hides(tmp_path):
    from jstdata.onboarding import set_tips_enabled, tips_enabled
    from jstdata.onboarding.ui import TipBox

    mark_prompted(tmp_path)
    set_tips_enabled(tmp_path, True)

    async def run():
        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert isinstance(app._tip_bar, TipBox) and app._tip_bar.is_attached
            home_tip = app._tip_shown
            await pilot.press("slash")
            await pilot.pause(0.5)
            # Tip text may stay if still on same screen class; hide/show is the contract.
            assert app._tip_bar is not None

            # A tutorial's coach replaces the tip line.
            app.start_tutorial(load_tutorial("big-tech-gross-profit"))
            await pilot.pause(0.5)
            assert app._tip_bar is None and app._coach_bar is not None
            app.leave_tutorial()
            await pilot.pause(0.5)
            assert app._tip_bar is not None

            # Clicking "✕ hide tips" turns them off and remembers it.
            await pilot.click("#tip-hide")
            await pilot.pause(0.5)
            assert app._tip_bar is None
            assert not tips_enabled(tmp_path)

            # :tips on brings them back.
            await pilot.press("colon")
            await pilot.pause()
            await pilot.press(*"tips", "space", "o", "n", "enter")
            await pilot.pause(0.5)
            assert app._tip_bar is not None and tips_enabled(tmp_path)

    asyncio.run(run())


def test_workspace_destination_highlights_first_item(tmp_path):
    from textual.widgets import ListView

    mark_prompted(tmp_path)

    async def run():
        _, app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.workspace.add_metric("gdp")
            app.workspace.add_entity("france")
            await pilot.press("4")
            await pilot.pause()
            assert app.screen.query_one("#home-list", ListView).index == 1
            await pilot.press("backspace")  # acts on the highlighted metric
            await pilot.pause(0.3)
            assert app.workspace.metric == []
            assert app.workspace.entity == ["france"]

    asyncio.run(run())


def test_tips_nudge_the_next_thing_to_collect():
    from jstdata.onboarding import tip_for

    search = dict(screen="HomeScreen", home_dest="search")
    one = tip_for(CoachSnapshot(entities=("a",), **search))
    assert "metric" not in one.split(".")[0]  # one entity: still the plain tip
    two = tip_for(CoachSnapshot(entities=("a", "b"), **search))
    assert two.startswith("You've collected 2 entities. Next, add a metric")
    assert 'press "/" then "Ctrl+U"' in two
    menu = tip_for(CoachSnapshot(screen="HomeScreen", entities=("a", "b")))
    assert 'press "2" to open Search' in menu
    metric_only = tip_for(CoachSnapshot(metrics=("m",), **search))
    assert "Next, add the companies or countries" in metric_only
    both = dict(metrics=("m",), entities=("a",))
    # "c" only works on the Workspace / Queries destinations; say how to get there.
    ws = tip_for(CoachSnapshot(screen="HomeScreen", home_dest="workspace", **both))
    assert 'Press "c"' in ws
    in_search = tip_for(CoachSnapshot(**both, **search))
    assert 'Keep adding with "Space"' in in_search and '"2" for Workspace' in in_search
    menu_ready = tip_for(CoachSnapshot(screen="HomeScreen", **both))
    assert 'Press "2" to review the workspace' in menu_ready
    # Query already saved: back to the destination tip.
    done = tip_for(
        CoachSnapshot(screen="HomeScreen", metrics=("m",), entities=("a",), query_count=1)
    )
    assert '"c"' not in done
    # Detail views keep their own tip; typing still wins everywhere.
    assert "Chart, Table, Sources" in tip_for(
        CoachSnapshot(screen="QueryScreen", entities=("a", "b"))
    )
    assert "typing" in tip_for(
        CoachSnapshot(focus_in_input=True, entities=("a", "b"), **search)
    )


def _finish(app):
    """Jump the running tutorial to its last phase and finish it."""
    app.coach.phase_index = len(app.coach.tutorial.phases) - 1
    app._finish_tutorial()


def test_finishing_shows_completion_popup_and_chains_tutorials(tmp_path):
    from jstdata.onboarding.ui import TutorialComplete

    mark_prompted(tmp_path)

    async def run():
        mine, app = _host(tmp_path, start_tutorial="big-tech-gross-profit")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            _finish(app)
            await pilot.pause(0.5)
            assert isinstance(app.screen, TutorialComplete)
            assert app.coach is None and app._coach_bar is None  # bar gone
            assert "big-tech-gross-profit" in completed_tutorials(tmp_path)
            assert app.screen.next_tutorial.id == "gulf-oil-exports"

            await pilot.press("enter")  # first row: next tutorial
            await pilot.pause(0.5)
            assert app.active.investigation == "tutorial-gulf-oil-exports"
            assert app.coach is not None

            _finish(app)
            await pilot.pause(0.5)
            await pilot.press("j", "j", "enter")  # next, menu, back
            await pilot.pause(0.5)
            # Back to the user's own investigation, not the previous tutorial.
            assert app.active.investigation == mine.id

    asyncio.run(run())


def test_completion_popup_keep_exploring(tmp_path):
    mark_prompted(tmp_path)

    async def run():
        _, app = _host(tmp_path, start_tutorial="g7-government-debt")
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            _finish(app)
            await pilot.pause(0.5)
            assert app.screen.next_tutorial is None  # last one
            await pilot.press("escape")  # keep exploring
            await pilot.pause(0.5)
            assert app.active.investigation == "tutorial-g7-government-debt"
            assert app.coach is None and app._coach_bar is None

    asyncio.run(run())
