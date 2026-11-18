"""`:publish` in the investigation TUI (JEF-141), with publishing faked."""

import asyncio

import pytest

from jstdata.investigation import add_query, open_investigation
from jstdata.investigation.publish_screens import (
    PublishConfirmModal,
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
    await pilot.pause()
    await pilot.press(*text, "enter")
    await pilot.pause(0.3)


def test_publish_confirms_then_shows_link(tmp_path, calls):
    async def run():
        app = _host(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _command(pilot, "publish")
            assert isinstance(app.screen, PublishConfirmModal)

            await pilot.press("y")
            for _ in range(20):
                await pilot.pause(0.1)
                if isinstance(app.screen, PublishedModal):
                    break
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
            await _command(pilot, "publish")
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
            await _command(pilot, "publish")
            await pilot.press("y")
            for _ in range(20):
                await pilot.pause(0.1)
                if not app._publishing:
                    break
            assert app._publishing is False
            assert not isinstance(app.screen, PublishedModal)
            assert len(calls) == 1

            # A failed publish doesn't block trying again.
            calls.error = None
            await _command(pilot, "publish")
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
