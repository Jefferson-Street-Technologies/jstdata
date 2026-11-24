"""Single-writer batch commits for a project directory.

No daemon. Callers stage project metadata and/or investigation YAML payloads,
then ``commit()`` under a lockfile with atomic renames.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

from .model import Investigation, InvestigationError
from .project import Project

LOCK_FILENAME = ".lock"
LOCK_STALE_SECONDS = 120


class ProjectLockError(InvestigationError):
    """Could not acquire the project lockfile."""


class ProjectBatchWriter:
    """Collect mutations for one project and flush them atomically."""

    def __init__(self, project_dir: Path, *, lock_timeout: float = 10.0):
        self.project_dir = Path(project_dir)
        self.lock_timeout = lock_timeout
        self._project: Optional[Project] = None
        self._investigations: dict[str, Investigation] = {}
        self._lock_fd: Optional[int] = None

    def stage_project(self, project: Project) -> None:
        project.validate()
        self._project = project

    def stage_investigation(self, inv: Investigation) -> None:
        inv.validate()
        self._investigations[inv.id] = inv

    def __enter__(self) -> ProjectBatchWriter:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None and (self._project or self._investigations):
                self.commit()
        finally:
            self.release()

    def acquire(self) -> None:
        if self._lock_fd is not None:
            return
        self.project_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.project_dir / LOCK_FILENAME
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode("utf-8"))
                self._lock_fd = fd
                return
            except FileExistsError:
                if _lock_is_stale(lock_path):
                    try:
                        lock_path.unlink()
                        continue
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    raise ProjectLockError(
                        f"Timed out waiting for project lock at {lock_path}."
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if self._lock_fd is None:
            return
        lock_path = self.project_dir / LOCK_FILENAME
        try:
            os.close(self._lock_fd)
        finally:
            self._lock_fd = None
            try:
                lock_path.unlink()
            except OSError:
                pass

    def commit(self) -> None:
        """Write staged files via temp + replace. Requires an held lock."""
        if self._lock_fd is None:
            raise ProjectLockError("commit() requires an acquired lock.")
        inv_dir = self.project_dir / "investigations"
        inv_dir.mkdir(parents=True, exist_ok=True)

        if self._project is not None:
            _atomic_yaml(self.project_dir / "project.yaml", self._project.to_dict())

        for inv in self._investigations.values():
            _atomic_yaml(inv_dir / f"{inv.id}.yaml", inv.to_dict())

        self._project = None
        self._investigations.clear()


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        text = lock_path.read_text(encoding="utf-8")
        lines = text.strip().splitlines()
        if len(lines) < 2:
            return True
        stamped = float(lines[1])
        return (time.time() - stamped) > LOCK_STALE_SECONDS
    except (OSError, ValueError):
        return True


def _atomic_yaml(target: Path, payload: Mapping[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                dict(payload),
                f,
                sort_keys=False,
                default_flow_style=False,
                allow_unicode=True,
            )
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
