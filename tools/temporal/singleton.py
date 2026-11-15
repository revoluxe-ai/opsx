"""Hard singleton: one running Temporal pipeline of a given identity.

Timestamped workflow IDs let Cursor retries / double slash starts enqueue a
second identical PP. A stable ID makes Temporal reject the duplicate while
the first execution is still running. After COMPLETED, the same ID may start
again (ALLOW_DUPLICATE reuse).

Operator `run` also takes a process lock so a second `tempo_operator.py run`
fails immediately without spawning another `start.py`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TextIO

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slug_token(value: str, *, max_len: int = 80) -> str:
    """Keep Temporal/workflow-id safe characters."""
    cleaned = (value or "").strip().replace("\\", "-").replace("/", "-")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", ".")
    cleaned = _SLUG_RE.sub("-", cleaned).strip("-._")
    return (cleaned or "unnamed")[:max_len]


def stable_workflow_id(
    prefix: str,
    mode: str,
    *,
    change: str | None = None,
) -> str:
    """Deterministic workflow id for one live PP.

    Hunt/verification include change so two different changes are distinct PPs.
    Bugbot/security are one-per-lane.
    """
    parts = [slug_token(prefix), slug_token(mode)]
    if change and change.strip():
        parts.append(slug_token(change))
    return "-".join(parts)


def pipeline_lock_path(repo: Path, pipeline_id: str, *, change: str | None = None) -> Path:
    key = slug_token(pipeline_id)
    if change and change.strip():
        key = f"{key}-{slug_token(change)}"
    return Path(repo) / ".tmp" / "temporal" / "locks" / f"{key}.lock"


class PipelineLockHeld(RuntimeError):
    """Another operator process already holds this pipeline lock."""


class PipelineLock:
    """Non-blocking exclusive lock. OS releases it if the process dies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: TextIO | None = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+", encoding="utf-8")
        try:
            _lock_exclusive_nb(fh)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        self._fh = fh
        return True

    def release(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            _unlock(fh)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass

    def __enter__(self) -> PipelineLock:
        if not self.acquire():
            raise PipelineLockHeld(str(self.path))
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


def _lock_exclusive_nb(fh: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        if fh.read(1) == "":
            fh.write("0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: TextIO) -> None:
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
