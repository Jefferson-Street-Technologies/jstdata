"""``dd`` deletes the selected remark on the query workbench."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from jstdata.actions.query_view import QueryScreen
from jstdata.investigation.api import (
    add_query,
    open_investigation,
    resume_or_create_investigation,
)
from jstdata.investigation.host import InvestigationHost
from jstdata.ops import query_ops


def _bare_host() -> InvestigationHost:
    host = InvestigationHost.__new__(InvestigationHost)
    host._pending_d = False
    host._d_timer = None
    host._invalid = False
    host.active = MagicMock()
    host.investigation = MagicMock()
    return host


def test_host_dd_prefers_selected_remark():
    host = _bare_host()
    screen = MagicMock()
    screen.delete_selected_remark.return_value = True
    with patch.object(
        InvestigationHost, "screen", new_callable=PropertyMock
    ) as mock_screen:
        mock_screen.return_value = screen
        with patch.object(host, "_delete_action_allowed", return_value=True):
            with patch(
                "jstdata.investigation.host.remove_action"
            ) as remove:
                host.action_delete_action()
                screen.delete_selected_remark.assert_called_once_with()
                remove.assert_not_called()


def test_host_dd_falls_through_without_selected_remark():
    host = _bare_host()
    host.active.action = 3
    host.app_dir = Path("/tmp")
    screen = MagicMock()
    screen.delete_selected_remark.return_value = False
    with patch.object(
        InvestigationHost, "screen", new_callable=PropertyMock
    ) as mock_screen:
        mock_screen.return_value = screen
        with patch.object(host, "_delete_action_allowed", return_value=True):
            with patch(
                "jstdata.investigation.host.remove_action",
                return_value=[3],
            ) as remove:
                with patch(
                    "jstdata.investigation.host.load_active",
                    return_value=host.active,
                ):
                    with patch.object(host, "notify"):
                        with patch.object(host, "_show_current_action"):
                            host.investigation.bfs_order.return_value = []
                            host.action_delete_action()
                            remove.assert_called_once()


def test_delete_selected_remark_noop_when_none():
    screen = QueryScreen.__new__(QueryScreen)
    screen._selected_remark_i = None
    assert screen.delete_selected_remark() is False


def test_delete_selected_remark_calls_action():
    screen = QueryScreen.__new__(QueryScreen)
    screen._selected_remark_i = 2
    screen.action_delete_remark = MagicMock()
    assert screen.delete_selected_remark() is True
    screen.action_delete_remark.assert_called_once_with(2)


def test_action_delete_remark_removes_and_clears_pick(tmp_path: Path):
    inv = resume_or_create_investigation(app_dir=tmp_path)
    q = add_query(
        inv,
        name="Debt",
        metric=["long-term-debt"],
        entity=["cik:1"],
        app_dir=tmp_path,
    )
    for text, name in (
        ("first note", "A"),
        ("second note", "B"),
        ("third", "C"),
    ):
        query_ops.remark_add_op(
            int(q.id),
            text,
            name=name,
            investigation=inv.id,
            app_dir=tmp_path,
        )

    screen = QueryScreen.__new__(QueryScreen)
    screen.query_id = int(q.id)
    screen._selected_remark_i = 2
    screen._remark_pick_mode = True
    screen.notify = MagicMock()
    screen._render_annotations = MagicMock()
    screen._resync_after_op = MagicMock()
    screen._exit_remark_pick = MagicMock()
    screen._op_target = MagicMock(
        return_value={
            "project": None,
            "investigation": inv.id,
            "app_dir": tmp_path,
        }
    )
    with patch.object(
        QueryScreen, "app", new_callable=PropertyMock
    ) as mock_app:
        mock_app.return_value = MagicMock()
        screen.action_delete_remark(2)

    inv2 = open_investigation(inv.id, app_dir=tmp_path)
    texts = [r.text for r in inv2.queries[q.id].remarks]
    assert texts == ["first note", "third"]
    screen._exit_remark_pick.assert_called_once_with(restore=True)
    screen.notify.assert_any_call("Deleted remark 2")
