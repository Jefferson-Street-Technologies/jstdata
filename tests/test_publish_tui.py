"""`:publish` in the investigation TUI (JEF-141), with publishing faked."""

import asyncio

import pytest

from jstdata.investigation import add_query, open_investigation
from jstdata.investigation.publish_screens import (
    PublishConfirmModal,
    PublishDraftScreen,
    PublishedModal,
    describe_publication,
)
from jstdata.onboarding import mark_prompted
from jstdata.publish import PublishError

URL = "https://site.test/p/AbCdEf1234"


class _Client:
    """Enough of JSTDataClient for the host: a key, and empty API calls."""

    api_key = "k123"

    class _cfg:  # noqa: N801 - mirrors JSTDataClient._cfg
        @staticmethod
        def read():
            return {"site_url": "https://site.test"}

    def __getattr__(self, name):
        return lambda *args, **kwargs: []


@pytest.fixture
def calls(monkeypatch):
    """Records publish calls; set `calls.error` to make the next one fail."""

    class Calls(list):
        error = None

    recorded = Calls()

    def fake_publish(inv, *, api_key, site_url, publisher_name=None):
        recorded.append({"id": inv.id, "api_key": api_key, "site_url": site_url,
                         "queries": sorted(inv.queries)})
        if recorded.error:
            raise PublishError(recorded.error)
        return {"id": "AbCdEf1234", "url": URL, "recordCount": 4}

    monkeypatch.setattr("jstdata.publish.publish_investigation", fake_publish)
    monkeypatch.delenv("JSTDATA_SITE_URL", raising=False)
    return recorded


def _host(tmp_path, *, with_query=True):
    from jstdata.investigation import resume_or_create_investigation
    from jstdata.investigation.host import InvestigationHost

    mark_prompted(tmp_path)  # skip the first-run tutorial menu
    inv = resume_or_create_investigation(app_dir=tmp_path)
    if with_query:
        add_query(inv, name="Debt", metric=["long-term-debt"], entity=["cik:1"], tail=4, app_dir=tmp_path)
    return InvestigationHost(_Client(), app_dir=tmp_path)


async def _command(pilot, text):
    await pilot.press("colon")
    await pilot.pause(0.2)  # let the palette mount before typing
    await pilot.press(*text, "enter")
    await pilot.pause(0.3)


async def _wait_for(pilot, predicate, seconds=3.0):
    """Poll instead of a fixed sleep, so slow machines don't flake."""
    for _ in range(int(seconds / 0.1)):
        if predicate():
            return
        await pilot.pause(0.1)


async def _to_confirm(pilot, app):
    """`:publish`, accept the draft as-is, and land on the confirmation."""
    await _command(pilot, "publish")
    await _wait_for(pilot, lambda: isinstance(app.screen, PublishDraftScreen))
    assert isinstance(app.screen, PublishDraftScreen)
    await pilot.press("ctrl+s")
    await _wait_for(pilot, lambda: isinstance(app.screen, PublishConfirmModal))


def test_publish_confirms_then_shows_link(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _to_confirm(pilot, app)
            assert isinstance(app.screen, PublishConfirmModal)

            await pilot.press("y")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishedModal))
            assert isinstance(app.screen, PublishedModal)
            assert app.screen.url == URL
            assert calls == [{"id": app.investigation.id, "api_key": "k123",
                              "site_url": "https://site.test", "queries": [1]}]

            await pilot.press("c")  # copy must not crash without a clipboard
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, PublishedModal)
            assert app._publishing is False

    asyncio.run(run())


def test_publish_cancel_sends_nothing(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _to_confirm(pilot, app)
            await pilot.press("n")
            await pilot.pause(0.3)
            assert not isinstance(app.screen, PublishConfirmModal)
            assert calls == []

    asyncio.run(run())


def test_publish_without_queries_explains_instead(tmp_path, calls):
    async def run():
        app = _host(tmp_path, with_query=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _command(pilot, "publish")
            assert not isinstance(app.screen, PublishConfirmModal)
            assert calls == []

    asyncio.run(run())


def test_publish_error_is_shown_and_allows_retry(tmp_path, calls):
    calls.error = "Query 1 was rejected by the API: Unknown metric"

    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _to_confirm(pilot, app)
            await pilot.press("y")
            await pilot.pause(0.1)
            await _wait_for(pilot, lambda: not app._publishing)
            assert app._publishing is False
            assert not isinstance(app.screen, PublishedModal)
            assert len(calls) == 1

            # A failed publish doesn't block trying again.
            calls.error = None
            await _to_confirm(pilot, app)
            assert isinstance(app.screen, PublishConfirmModal)

    asyncio.run(run())


def test_confirmation_lists_what_becomes_public(tmp_path):
    from jstdata.investigation import resume_or_create_investigation

    inv = resume_or_create_investigation(app_dir=tmp_path)
    for i in range(10):
        add_query(inv, name=f"[red]Q{i}", metric=["m"], app_dir=tmp_path)
    inv = open_investigation(inv.id, app_dir=tmp_path)
    text = describe_publication(inv)
    assert "10 queries" in text
    assert "… and 2 more" in text
    # Names that look like Rich markup are escaped, not applied.
    assert "\\[red]Q0" in text
    assert "Anyone with the link" in text


def test_markup_like_text_does_not_crash_the_tui(tmp_path, calls):
    """Cloned investigations are remote text; brackets must not break rendering."""
    from jstdata.actions.query_view import QueryScreen
    from jstdata.investigation import update_investigation
    from jstdata.investigation.host import AnnotationModal, InvestigationHost
    from jstdata.investigation import resume_or_create_investigation

    mark_prompted(tmp_path)
    inv = resume_or_create_investigation(app_dir=tmp_path)
    update_investigation(
        inv,
        title="Q3 [/] debt [link=https://evil]x[/link]",
        notes="notes [/bold] here",
        annotations="ann [/] x",
        app_dir=tmp_path,
    )
    inv = open_investigation(inv.id, app_dir=tmp_path)
    add_query(inv, name="query [/] name", annotations="qa [/x] y", metric=["m"], app_dir=tmp_path)

    async def run():
        app = InvestigationHost(_Client(), app_dir=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.4)
            await _command(pilot, "query 1")
            await _wait_for(pilot, lambda: isinstance(app.screen, QueryScreen))
            assert isinstance(app.screen, QueryScreen)
            await _command(pilot, "annotate 1")
            await _wait_for(pilot, lambda: isinstance(app.screen, AnnotationModal))
            assert isinstance(app.screen, AnnotationModal)
            await pilot.press("escape")
            await pilot.pause(0.2)
            # Every Home section renders titles, notes and query names.
            for key in ["1", "2", "3", "4", "5"]:
                await pilot.press("g")
                await pilot.pause(0.2)
                await pilot.press(key)
                await pilot.pause(0.3)
        # The publish dialog's escaping is covered by
        # test_confirmation_lists_what_becomes_public.
        assert app._exception is None

    asyncio.run(run())


def test_publish_aborts_if_investigation_changes_while_confirming(tmp_path, calls):
    from jstdata.investigation import create_investigation

    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _to_confirm(pilot, app)
            assert isinstance(app.screen, PublishConfirmModal)
            # Another investigation becomes current (e.g. `jst open` elsewhere).
            app.investigation = create_investigation(app_dir=tmp_path, activate=False)
            await pilot.press("y")
            await pilot.pause(0.5)
            assert calls == []
            assert not isinstance(app.screen, PublishedModal)

    asyncio.run(run())



def test_draft_text_is_saved_then_published(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _command(pilot, "publish")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishDraftScreen))
            area = app.screen.query_one("#draft-body")
            area.text = area.text.replace(
                "What question does this answer, and what did you find?",
                "## Finding\nDebt rose.",
            ).replace("What should a reader notice in this chart?", "Look at 2020.")
            await pilot.press("ctrl+s")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishConfirmModal))
            assert isinstance(app.screen, PublishConfirmModal)
            # Saved to the investigation file before publishing.
            saved = open_investigation(app.investigation.id, app_dir=tmp_path)
            assert saved.annotations == "## Finding\nDebt rose."
            assert saved.queries[1].annotations == "Look at 2020."
            await pilot.press("y")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishedModal))
            assert len(calls) == 1

    asyncio.run(run())


def test_cancelling_the_draft_publishes_nothing(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _command(pilot, "publish")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishDraftScreen))
            await pilot.press("escape")
            await pilot.pause(0.3)
            assert not isinstance(app.screen, (PublishDraftScreen, PublishConfirmModal))
            assert calls == []

    asyncio.run(run())


def test_bad_draft_reopens_with_the_text_and_error(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await _command(pilot, "publish")
            await _wait_for(pilot, lambda: isinstance(app.screen, PublishDraftScreen))
            area = app.screen.query_one("#draft-body")
            area.text = area.text + "\n## Query 9: Typo\nmy note\n"
            await pilot.press("ctrl+s")
            await _wait_for(pilot, lambda: app.screen.query("#draft-error"))
            assert isinstance(app.screen, PublishDraftScreen)
            assert "Query 9" in str(app.screen.query_one("#draft-error").renderable)
            assert "my note" in app.screen.query_one("#draft-body").text
            assert calls == []

    asyncio.run(run())
