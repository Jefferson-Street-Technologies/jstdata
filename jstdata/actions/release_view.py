"""Release View — provenance for an observation (filing / release).

Idle until ``r``. Input Context should carry ``release`` (one id).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Label, Static

from ..client import JSTDataClient
from ..investigation.model import ResearchContext, Workspace
from ..models import Release
from .base import ActionBinding, ActionSpec, register
from .screen import BaseScreen


class ReleaseScreen(BaseScreen):
    """Show release / filing metadata for the focused release id."""

    BINDINGS = [
        Binding("r", "load", "Load", key_display="r"),
    ]

    DEFAULT_CSS = """
    #release-body {
        height: 1fr;
    }
    .pane-container {
        border: solid #222;
        background: #0f0f0f;
        height: 1fr;
    }
    .pane-header {
        background: #1a1a1a;
        color: #4ade80;
        padding: 0 1;
        text-style: bold;
        height: 1;
    }
    #release-fields {
        color: #e0e0e0;
        padding: 1 1 0 1;
        height: auto;
    }
    #release-status {
        color: #888;
        padding: 0 1;
        height: 1;
    }
    #release-hint {
        color: #666;
        padding: 1 1;
        height: auto;
    }
    """

    def __init__(
        self,
        client: JSTDataClient,
        workspace: Workspace,
        *,
        input_context: Optional[ResearchContext] = None,
        **kwargs,
    ) -> None:
        super().__init__()
        self.client = client
        self.workspace = workspace
        self._input_context = input_context
        self._release_id: Optional[str] = None
        self._release: Optional[Release] = None
        self._loaded = False

    def _resolved_input_context(self) -> ResearchContext:
        if self._input_context is not None:
            return self._input_context
        app_ctx = getattr(self.app, "input_context", None)
        if isinstance(app_ctx, ResearchContext):
            return app_ctx
        return ResearchContext.from_workspace(self.workspace)

    def compose_body(self) -> ComposeResult:
        with Vertical(id="release-body"):
            with Vertical(classes="pane-container"):
                yield Label("RELEASE // PROVENANCE", classes="pane-header")
                yield Static("", id="release-fields", markup=True)
                yield Static(
                    "Idle — press [bold]r[/bold] to load",
                    id="release-status",
                    markup=True,
                )
                yield Static(
                    "This is the filing / release that produced an observation.\n"
                    "Drill here from a table cell after loading values (r).",
                    id="release-hint",
                    markup=True,
                )

    def on_mount(self) -> None:
        super().on_mount()
        ctx = self._resolved_input_context()
        if not ctx.release:
            self.query_one("#release-fields", Static).update(
                "No release in Input Context.\n"
                "Drill into a table cell that has a release_id, "
                "or set context.release in YAML."
            )
            self.query_one("#release-status", Static).update("")
            return
        self._release_id = ctx.release[0]
        bits = [f"[bold]id[/bold]  {self._release_id}"]
        if ctx.entity:
            bits.append(f"[bold]entity[/bold]  {', '.join(ctx.entity[:3])}")
        if ctx.metric:
            bits.append(f"[bold]metric[/bold]  {', '.join(ctx.metric[:3])}")
        self.query_one("#release-fields", Static).update("\n".join(bits))

    def footer_action_keys(self) -> list[str]:
        return ["[bold]r[/bold] load"]

    def action_load(self) -> None:
        if not self._release_id:
            self.notify("No release in Input Context", severity="warning")
            return
        self.load_release()

    def _render_release(self, release: Release) -> None:
        ctx = self._resolved_input_context()
        lines = [
            f"[bold]id[/bold]      {release.id}",
            f"[bold]label[/bold]   {release.label or '—'}",
            f"[bold]date[/bold]    {release.release_date.isoformat()}",
            f"[bold]source[/bold]  {release.source or '—'}",
        ]
        if ctx.entity:
            lines.append(f"[bold]entity[/bold]  {', '.join(ctx.entity)}")
        if ctx.metric:
            lines.append(f"[bold]metric[/bold]  {', '.join(ctx.metric)}")
        self.query_one("#release-fields", Static).update("\n".join(lines))

    @work(exclusive=True, group="release-view")
    async def load_release(self) -> None:
        release_id = self._release_id
        if not release_id:
            return
        status = self.query_one("#release-status", Static)
        status.update("Loading…")
        try:
            release = await asyncio.to_thread(self.client.get_release, release_id)
        except Exception as exc:
            status.update(f"Could not load release: {exc}")
            return
        if release_id != self._release_id:
            return
        self._release = release
        self._loaded = True
        self._render_release(release)
        if hasattr(self.app, "remember_label"):
            self.app.remember_label(release.id, release.label or release.id)
        status.update(f"Loaded · {release.source or 'release'} · {release.release_date}")
        self.refresh_footer()


def create_release_screen(client, workspace, input_context=None, **kwargs):
    return ReleaseScreen(client, workspace, input_context=input_context)


RELEASE_VIEW = register(
    ActionSpec(
        id="release",
        name="Release",
        description=(
            "Inspect a release / filing behind an observation "
            "(label, date, source). Idle until load (r)."
        ),
        create_screen=create_release_screen,
        arguments=(),
        bindings=(
            ActionBinding("r", "load", "Load release metadata"),
        ),
        example="jst action add release",
    )
)
