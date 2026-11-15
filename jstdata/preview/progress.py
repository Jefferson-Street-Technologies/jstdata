"""Terminal progress spinner for ``jst preview``."""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional, TextIO


_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ProgressSpinner:
    """Animated status line for long-running preview work.

    Usage::

        with ProgressSpinner() as progress:
            progress.update("Fetching series…")
            ...
            progress.succeed("Wrote preview.html")
    """

    def __init__(
        self,
        *,
        stream: Optional[TextIO] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stderr
        if enabled is None:
            enabled = bool(getattr(self._stream, "isatty", lambda: False)())
        self._enabled = enabled
        self._message = "Working…"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active = False
        self._last_width = 0

    def __enter__(self) -> "ProgressSpinner":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # SystemExit is raised by click/sys.exit after we already reported.
        if exc_type is None or issubclass(exc_type, SystemExit):
            if self._active:
                self.stop()
            return None
        if self._active:
            self.fail(str(exc) if exc else self._message)
        return None

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._stop.clear()
        if self._enabled:
            self._thread = threading.Thread(
                target=self._spin, name="jst-preview-spinner", daemon=True
            )
            self._thread.start()
        else:
            self._writeln(f"· {self._message}")

    def update(self, message: str) -> None:
        """Replace the spinner status text."""
        message = message.strip() or self._message
        with self._lock:
            self._message = message
        if not self._enabled:
            self._writeln(f"· {message}")

    def succeed(self, message: Optional[str] = None) -> None:
        text = (message or self._message).strip()
        self.stop()
        self._writeln(f"✓ {text}")

    def fail(self, message: Optional[str] = None) -> None:
        text = (message or self._message).strip()
        self.stop()
        self._writeln(f"✗ {text}")

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self._enabled:
            self._clear_line()

    def _spin(self) -> None:
        idx = 0
        while not self._stop.wait(0.08):
            with self._lock:
                message = self._message
            frame = _FRAMES[idx % len(_FRAMES)]
            idx += 1
            self._write_status(f"{frame} {message}")

    def _write_status(self, text: str) -> None:
        # Pad/clear previous longer lines so leftovers don't linger.
        pad = max(0, self._last_width - len(text))
        self._stream.write("\r" + text + (" " * pad))
        self._stream.flush()
        self._last_width = len(text)

    def _clear_line(self) -> None:
        if self._last_width:
            self._stream.write("\r" + (" " * self._last_width) + "\r")
            self._stream.flush()
            self._last_width = 0

    def _writeln(self, text: str) -> None:
        if self._enabled and self._last_width:
            self._clear_line()
        self._stream.write(text + "\n")
        self._stream.flush()
