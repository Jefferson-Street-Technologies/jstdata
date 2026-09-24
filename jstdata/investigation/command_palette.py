"""``:`` command palette — discovery, history, autocomplete, and dispatch."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .commands import (
    COMMANDS,
    CommandError,
    action_arg_doc_lines,
    complete_command_line,
    completion_doc,
    list_command_suggestions,
    parse_command_line,
)


class CommandPalette(ModalScreen[str | None]):
    """Transparent bottom command line. Returns the submitted line or None."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center bottom;
        background: transparent;
    }

    #cmd-panel {
        width: 100%;
        height: auto;
        max-height: 14;
        background: #111111;
        border-top: tall #4ade80;
        padding: 0 1 1 1;
    }

    #cmd-suggestions {
        height: auto;
        max-height: 14;
        color: #c0c0c0;
        padding: 0 1;
    }

    #cmd-input {
        width: 100%;
        background: #0a0a0a;
        border: solid #333;
    }

    #cmd-input.-invalid {
        border: solid #f87171;
        color: #f87171;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=False),
        Binding("up", "history_prev", "History", show=False),
        Binding("down", "history_next", "History", show=False),
        Binding("tab", "complete", "Complete", show=False, priority=True),
        Binding(
            "shift+tab", "complete_prev", "Complete prev", show=False, priority=True
        ),
        Binding("space", "commit_completion", "Lock suggestion", show=False, priority=True),
        Binding("enter", "commit_or_submit", "Lock or run", show=False, priority=True),
    ]

    def __init__(self, history: list[str] | None = None) -> None:
        super().__init__()
        self.history = list(history or [])
        self._hist_index: int | None = None
        self._draft = ""
        self._completions: list[str] = []
        self._comp_index: int = -1
        # Input.Changed can fire after programmatic sets; remember the value we
        # applied so a deferred event does not clear the Tab-cycle freeze.
        self._applied_value: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="cmd-panel"):
            yield Static(self._format_suggestions(""), id="cmd-suggestions", markup=True)
            yield Input(
                placeholder="Tab cycle · Space/Enter lock · Enter runs when idle",
                id="cmd-input",
            )

    def on_mount(self) -> None:
        inp = self.query_one("#cmd-input", Input)
        inp.value = ""
        inp.focus()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Let the Input type a normal space unless a suggestion is highlighted.
        if action == "commit_completion" and not self._has_highlight():
            return False
        return True

    def _has_highlight(self) -> bool:
        return bool(self._completions) and self._comp_index >= 0

    def _body(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith(":"):
            return stripped[1:].lstrip()
        return stripped

    def _candidate_list(self, text: str) -> list[str]:
        if self._completions:
            return self._completions
        return complete_command_line(text)

    def _escape_markup(self, text: str) -> str:
        return text.replace("[", "\\[")

    def _format_completion_row(
        self, cand: str, *, highlighted: bool, show_args: bool
    ) -> list[str]:
        """Candidate line (+ optional arg docs for an action type)."""
        safe = self._escape_markup(cand)
        doc = self._escape_markup(completion_doc(cand))
        if highlighted:
            head = f"[reverse bold #0a0a0a on #4ade80]{safe}[/]"
            row = f"{head}  {doc}" if doc else head
        else:
            row = f"{safe:<28}  {doc}" if doc else safe
        rows = [row]
        tokens = cand.split()
        if (
            show_args
            and len(tokens) == 2
            and tokens[0].lower() == "add"
            and not tokens[1].startswith("-")
        ):
            for line in action_arg_doc_lines(tokens[1]):
                rows.append(self._escape_markup(line))
        return rows

    def _format_suggestions(self, text: str) -> str:
        body = self._body(text)
        parts = body.split(None, 1) if body else []
        completions = self._candidate_list(text)
        if completions:
            lines: list[str] = []
            # When completing flags for `add <type>`, show that type's args once.
            add_type = self._shared_add_action_type(completions)
            if add_type and self._comp_index < 0:
                lines.append(f"  args for {add_type}:")
                lines.extend(
                    self._escape_markup(line)
                    for line in action_arg_doc_lines(add_type)
                )
            action_type_cands = [
                c
                for c in completions
                if len(c.split()) == 2
                and c.split()[0].lower() == "add"
                and not c.split()[1].startswith("-")
            ]
            # Expand args for the highlighted type, or the sole action-type match.
            expand_all_types = len(action_type_cands) == 1
            for i, cand in enumerate(completions):
                show_args = (i == self._comp_index) or (
                    expand_all_types and cand in action_type_cands
                )
                lines.extend(
                    self._format_completion_row(
                        cand,
                        highlighted=(i == self._comp_index),
                        show_args=show_args,
                    )
                )
            return "\n".join(lines)
        if parts and parts[0] in {c.name for c in COMMANDS}:
            match = next(c for c in COMMANDS if c.name == parts[0])
            if len(parts) > 1 or text.rstrip() != text or body.endswith(" "):
                return match.help_line()
        prefix = parts[0] if parts else ""
        specs = list_command_suggestions(prefix)
        if not specs:
            return ""
        return "\n".join(s.help_line() for s in specs)

    @staticmethod
    def _shared_add_action_type(completions: list[str]) -> str | None:
        """If every candidate is ``add <type> --flag``, return ``<type>``."""
        found: str | None = None
        for cand in completions:
            tokens = cand.split()
            if (
                len(tokens) < 3
                or tokens[0].lower() != "add"
                or tokens[1].startswith("-")
                or not tokens[-1].startswith("-")
            ):
                return None
            if found is None:
                found = tokens[1]
            elif found != tokens[1]:
                return None
        return found

    def _with_colon_style(self, completed: str, current: str) -> str:
        if current.lstrip().startswith(":"):
            return ":" + completed
        return completed

    def _set_input(self, value: str) -> None:
        """Set the input value without treating it as a destructive user edit."""
        inp = self.query_one("#cmd-input", Input)
        self._applied_value = value
        inp.value = value
        inp.cursor_position = len(value)

    def _set_input_invalid(self, invalid: bool) -> None:
        inp = self.query_one("#cmd-input", Input)
        inp.set_class(invalid, "-invalid")

    def _refresh_suggestions(self, text: str) -> None:
        self.query_one("#cmd-suggestions", Static).update(
            self._format_suggestions(text)
        )

    def _apply_completion(self, delta: int) -> None:
        inp = self.query_one("#cmd-input", Input)
        current = inp.value
        body = self._body(current)

        # Freeze on first Tab; later Tabs only move within that list.
        if not self._completions:
            self._completions = complete_command_line(current)
            if not self._completions:
                self._comp_index = -1
                self._refresh_suggestions(current)
                return
            if body in self._completions:
                self._comp_index = self._completions.index(body)
            else:
                self._comp_index = -1

        self._comp_index = (self._comp_index + delta) % len(self._completions)
        choice = self._completions[self._comp_index]
        self._set_input(self._with_colon_style(choice, current))
        self._refresh_suggestions(inp.value)

    def _lock_in_and_advance(self) -> None:
        """Commit the highlighted suggestion and load the next token's options."""
        if not self._has_highlight():
            return
        inp = self.query_one("#cmd-input", Input)
        current = inp.value
        choice = self._completions[self._comp_index].rstrip() + " "
        styled = self._with_colon_style(choice, current)

        self._completions = []
        self._comp_index = -1
        self._set_input(styled)
        # Next token candidates (unhighlighted until the user Tabs).
        self._completions = complete_command_line(styled)
        self._comp_index = -1
        self._refresh_suggestions(styled)

    def _submit(self) -> None:
        raw = self.query_one("#cmd-input", Input).value.strip()
        if not raw:
            self.dismiss(None)
            return
        try:
            parse_command_line(raw)
        except CommandError:
            self._set_input_invalid(True)
            return
        self._set_input_invalid(False)
        hist = raw[1:].strip() if raw.startswith(":") else raw
        self.dismiss(hist)

    def action_complete(self) -> None:
        self._apply_completion(+1)

    def action_complete_prev(self) -> None:
        self._apply_completion(-1)

    def action_commit_completion(self) -> None:
        self._lock_in_and_advance()

    def action_commit_or_submit(self) -> None:
        if self._has_highlight():
            self._lock_in_and_advance()
        else:
            self._submit()

    @on(Input.Changed, "#cmd-input")
    def on_changed(self, event: Input.Changed) -> None:
        # Programmatic Tab/lock/history updates — keep the freeze.
        if self._applied_value is not None and event.value == self._applied_value:
            self._applied_value = None
            self._refresh_suggestions(event.value)
            return

        self._applied_value = None
        # Real edit: drop Tab-cycle freeze so suggestions track the input.
        self._completions = []
        self._comp_index = -1
        self._hist_index = None
        self._set_input_invalid(False)
        self._refresh_suggestions(event.value)

    @on(Input.Submitted, "#cmd-input")
    def on_submit(self, event: Input.Submitted) -> None:
        # Enter is handled by action_commit_or_submit (priority binding).
        # Keep this as a fallback if Submitted still fires.
        if self._has_highlight():
            self._lock_in_and_advance()
        else:
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_history_prev(self) -> None:
        if not self.history:
            return
        if self._hist_index is None:
            self._draft = self.query_one("#cmd-input", Input).value
            self._hist_index = len(self.history) - 1
        elif self._hist_index > 0:
            self._hist_index -= 1
        self._set_input(self.history[self._hist_index])

    def action_history_next(self) -> None:
        if self._hist_index is None:
            return
        if self._hist_index >= len(self.history) - 1:
            self._hist_index = None
            self._set_input(self._draft)
        else:
            self._hist_index += 1
            self._set_input(self.history[self._hist_index])
