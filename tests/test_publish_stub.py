"""Published success modal: copy feedback in the popup."""

from __future__ import annotations

import asyncio

from textual.app import App

from jstdata.investigation.publish_screens import PublishedModal


def test_published_modal_copy_feedback():
    """Pressing c flips clear in-popup copied state."""

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                PublishedModal("https://www.jeffersonst.io/p/AbCdEf1234")
            )

    async def run():
        app = _Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PublishedModal)
            assert "jeffersonst.io/p/AbCdEf1234" in str(
                screen.query_one("#published-url").render()
            )
            assert "copied" not in screen.query_one("#published-box").classes
            await pilot.press("c")
            await pilot.pause()
            assert "copied" in screen.query_one("#published-box").classes
            assert "copied" in screen.query_one("#published-url").classes
            status = str(screen.query_one("#published-copy-status").render())
            assert "COPIED TO CLIPBOARD" in status
            hint = str(screen.query_one("#published-hint").render())
            assert "COPIED TO CLIPBOARD" in hint

    asyncio.run(run())
