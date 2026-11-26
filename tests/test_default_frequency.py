"""TUI defaults to the first concrete frequency after Any."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jstdata.actions.query_view import QueryScreen
from jstdata.investigation.query_compose import (
    NULL_FREQUENCY,
    default_frequency,
    frequency_options_for,
)


def test_default_frequency_picks_first_after_any():
    assert default_frequency([None]) is None
    assert default_frequency([None, "Monthly", "Quarterly"]) == "Monthly"
    assert default_frequency([None, "Annual"]) == "Annual"
    assert default_frequency(["Daily"]) == "Daily"
    assert default_frequency([]) is None


def test_frequency_options_order_then_default():
    opts = frequency_options_for({"Quarterly", "Monthly", NULL_FREQUENCY})
    assert opts[0] is None
    assert default_frequency(opts) == "Monthly"
    assert opts[-1] == NULL_FREQUENCY


def test_refresh_applies_default_once_per_composition():
    screen = QueryScreen.__new__(QueryScreen)
    screen._freq_options = [None]
    screen._freq_source_key = ("m:gdp",)
    screen._freq_auto_key = None
    screen.query_id = 1
    screen.client = MagicMock()
    screen._op_target = MagicMock(return_value={})
    screen._paint_compose = MagicMock()
    screen._apply_default_frequency = MagicMock()
    screen._freq_source_key_for = MagicMock(return_value=("m:gdp",))
    screen._resolve_query = MagicMock(
        return_value=MagicMock(frequency=None, metric=["gdp"], series=[])
    )

    # Simulate the post-fetch branch of refresh_frequency_options.
    available = {"Monthly", "Quarterly"}
    screen._freq_options = frequency_options_for(available)
    preferred = default_frequency(screen._freq_options)
    assert preferred == "Monthly"
    if (
        preferred is not None
        and screen._resolve_query().frequency is None
        and screen._freq_auto_key != screen._freq_source_key
    ):
        screen._freq_auto_key = screen._freq_source_key
        screen._apply_default_frequency(preferred)

    screen._apply_default_frequency.assert_called_once_with("Monthly")
    assert screen._freq_auto_key == ("m:gdp",)

    # Choosing Any later (frequency still None) must not re-apply.
    screen._apply_default_frequency.reset_mock()
    preferred = default_frequency(screen._freq_options)
    if (
        preferred is not None
        and screen._resolve_query().frequency is None
        and screen._freq_auto_key != screen._freq_source_key
    ):
        screen._apply_default_frequency(preferred)
    screen._apply_default_frequency.assert_not_called()


def test_apply_default_frequency_patches_saved_query():
    screen = QueryScreen.__new__(QueryScreen)
    screen.query_id = 7
    screen._op_target = MagicMock(return_value={"app_dir": "/tmp"})
    screen._resync_after_op = MagicMock()
    screen._mark_composition_changed = MagicMock()
    screen._paint_compose = MagicMock()
    screen.notify = MagicMock()

    with patch("jstdata.ops.query_ops.patch_query_op") as patch_op:
        screen._apply_default_frequency("Quarterly")
        patch_op.assert_called_once()
        assert patch_op.call_args.args[0] == 7
        assert patch_op.call_args.kwargs["frequency"] == "Quarterly"
    screen._mark_composition_changed.assert_called_once()
    screen._paint_compose.assert_called_once()
