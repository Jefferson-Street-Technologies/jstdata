"""Chart time-bound mouse drag helpers and QueryScreen wiring."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from jstdata.actions.query_view import (
    BOUND_HIT_RADIUS,
    HistoryPlot,
    QueryScreen,
    canvas_region_for_width,
    date_index_to_x,
    nearest_bound_to_drag,
    x_to_date_index,
)


def test_canvas_region_for_width():
    left, right = canvas_region_for_width(80)
    assert left == 10
    assert right == 79
    left, right = canvas_region_for_width(20)
    assert 0 <= left <= right
    assert right == 19


def test_x_to_date_index_and_back():
    n = 11
    left, right = 0, 100
    assert x_to_date_index(0, n, left, right) == 0
    assert x_to_date_index(100, n, left, right) == 10
    assert x_to_date_index(50, n, left, right) == 5
    # Clamps outside the canvas.
    assert x_to_date_index(-5, n, left, right) == 0
    assert x_to_date_index(999, n, left, right) == 10
    assert date_index_to_x(0, n, left, right) == 0
    assert date_index_to_x(10, n, left, right) == 100
    assert date_index_to_x(5, n, left, right) == 50
    assert x_to_date_index(0, 1, left, right) == 0
    assert x_to_date_index(50, 0, left, right) == 0


def test_nearest_bound_to_drag_prefers_hit_radius():
    # Within radius of lo (even if hi is focused).
    assert nearest_bound_to_drag(12, 10, 50, focused="hi", hit_radius=3) == "lo"
    # Within radius of hi (even if lo is focused).
    assert nearest_bound_to_drag(48, 10, 50, focused="lo", hit_radius=3) == "hi"
    # Outside both: place the Tab-focused bound.
    assert nearest_bound_to_drag(20, 10, 50, focused="hi", hit_radius=3) == "hi"
    assert nearest_bound_to_drag(40, 10, 50, focused="lo", hit_radius=3) == "lo"
    assert nearest_bound_to_drag(30, 10, 50, focused="hi", hit_radius=BOUND_HIT_RADIUS) == "hi"


def test_on_bound_drag_updates_lo_and_hi():
    screen = QueryScreen.__new__(QueryScreen)
    screen._bound_lo_i = 2
    screen._bound_hi_i = 8
    screen._bound_focus = "hi"
    screen._cursor_i = 8
    dates = [datetime(2020, 1, 1) + timedelta(days=30 * i) for i in range(10)]
    screen._all_cursor_dates = MagicMock(return_value=dates)
    screen._clamp_bounds = MagicMock()
    screen._render_chart = MagicMock()
    screen._render_obs_strip = MagicMock()

    chart = MagicMock()
    with patch.object(QueryScreen, "query_one", return_value=chart):
        event = HistoryPlot.BoundDrag("lo", 5)
        event.stop = MagicMock()
        screen._on_bound_drag(event)
        assert screen._bound_lo_i == 5
        assert screen._bound_hi_i == 8
        assert screen._bound_focus == "lo"
        assert screen._cursor_i == 5
        event.stop.assert_called_once()
        screen._render_chart.assert_called()
        screen._render_obs_strip.assert_called()

        event2 = HistoryPlot.BoundDrag("hi", 3)
        event2.stop = MagicMock()
        # hi drag cannot cross below lo (clamped to lo).
        screen._on_bound_drag(event2)
        assert screen._bound_hi_i == 5
        assert screen._bound_focus == "hi"


def test_history_plot_mouse_down_respects_focus_when_not_on_marker():
    plot = HistoryPlot.__new__(HistoryPlot)
    plot._drag_which = None
    plot._drag_dates_n = 11
    plot._bound_lo_i = 0
    plot._bound_hi_i = 10
    plot._bound_focus = "hi"
    plot._canvas_left = 0
    plot._canvas_right = 100
    plot._refresh_canvas_region = MagicMock()
    plot.capture_mouse = MagicMock()
    plot.release_mouse = MagicMock()
    posted: list = []
    plot.post_message = MagicMock(side_effect=lambda m: posted.append(m))

    # Click mid-chart with hi focused → moves hi, not the nearer lo.
    down = MagicMock()
    down.button = 1
    down.x = 40
    down.stop = MagicMock()
    plot.on_mouse_down(down)
    assert plot._drag_which == "hi"
    assert posted and posted[0].which == "hi"


def test_history_plot_mouse_down_emits_bound_drag():
    plot = HistoryPlot.__new__(HistoryPlot)
    plot._drag_which = None
    plot._drag_dates_n = 11
    plot._bound_lo_i = 0
    plot._bound_hi_i = 10
    plot._bound_focus = "hi"
    plot._canvas_left = 0
    plot._canvas_right = 100
    plot._refresh_canvas_region = MagicMock()
    plot.capture_mouse = MagicMock()
    plot.release_mouse = MagicMock()
    posted: list = []
    plot.post_message = MagicMock(side_effect=lambda m: posted.append(m))

    down = MagicMock()
    down.button = 1
    down.x = 0  # near lo marker → grab lo even if hi focused
    down.stop = MagicMock()
    plot.on_mouse_down(down)
    assert plot._drag_which == "lo"
    plot.capture_mouse.assert_called_once()
    assert posted and posted[0].which == "lo" and posted[0].date_index == 0

    move = MagicMock()
    move.x = 50
    move.stop = MagicMock()
    plot.on_mouse_move(move)
    assert posted[-1].date_index == 5

    up = MagicMock()
    up.x = 100
    up.stop = MagicMock()
    plot.on_mouse_up(up)
    assert plot._drag_which is None
    plot.release_mouse.assert_called_once()
    assert posted[-1].date_index == 10
