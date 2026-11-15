"""OS-owned execution lease: process exit releases it without stale PID authority."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path

from .context import OpsxError, Project


class ExecutionLease:
    """Lock one byte on Windows or flock on POSIX; never kill a process."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise OpsxError("PROJECT_RUN_ACTIVE", "A project execution or installation is active.") from exc
        return self

    def release(self):
        if self.stream is not None:
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                self.stream = None


@asynccontextmanager
async def execution_lease(project: Project):
    lease = ExecutionLease(project.runs / "execution.lock")
    await asyncio.to_thread(lease.acquire)
    try:
        yield lease
    finally:
        await asyncio.to_thread(lease.release)
