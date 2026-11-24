"""Host ``dd`` chord must not permanently steal a lone ``d`` from screens."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from textual.binding import Binding

from jstdata.investigation.host import InvestigationHost


class _ScreenWithD:
    BINDINGS = (Binding("d", "toggle_direction", "Toggle"),)

    def __init__(self) -> None:
        self.toggled = 0

    def action_toggle_direction(self) -> None:
        self.toggled += 1


def _bare_host() -> InvestigationHost:
    host = InvestigationHost.__new__(InvestigationHost)
    host._pending_d = False
    host._d_timer = None
    return host


def test_screen_action_for_key_reads_binding():
    host = _bare_host()
    screen = _ScreenWithD()
    with patch.object(
        InvestigationHost, "screen", new_callable=PropertyMock
    ) as mock_screen:
        mock_screen.return_value = screen
        assert host._screen_action_for_key("d") == "toggle_direction"
        assert host._screen_action_for_key("x") is None


def test_expire_pending_d_forwards_to_screen_action():
    host = _bare_host()
    screen = _ScreenWithD()
    with patch.object(
        InvestigationHost, "screen", new_callable=PropertyMock
    ) as mock_screen:
        mock_screen.return_value = screen
        host._pending_d = True
        host._expire_pending_d()
        assert host._pending_d is False
        assert screen.toggled == 1


def test_expire_pending_d_noop_without_screen_binding():
    host = _bare_host()
    screen = MagicMock()
    screen.BINDINGS = ()
    with patch.object(
        InvestigationHost, "screen", new_callable=PropertyMock
    ) as mock_screen:
        mock_screen.return_value = screen
        host._pending_d = True
        host._expire_pending_d()
        assert host._pending_d is False
