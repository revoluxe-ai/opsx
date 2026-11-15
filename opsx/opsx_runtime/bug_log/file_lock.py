"""Межпроцессная блокировка файла журнала (Windows и POSIX)."""

from __future__ import annotations

import os
from pathlib import Path


class FileLock:
    """Эксклюзивная блокировка через lock-файл рядом с журналом."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: object | None = None

    def __enter__(self) -> FileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._path, "a+b")  # noqa: SIM115 — закрывается в __exit__
        self._fh = fh
        if fh.seek(0, 2) == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        fh = self._fh
        if fh is None:
            return
        if os.name == "nt":
            import msvcrt

            fh.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
        fh.close()  # type: ignore[attr-defined]
        self._fh = None
