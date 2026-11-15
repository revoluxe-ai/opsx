"""Дублирование stdout/stderr repair-hop в callback (UI live-log).

Пока активен ``stream_process_output``, каждая запись в ``sys.stdout`` /
``sys.stderr`` (в т.ч. ``print(..., file=sys.stderr)`` из Cursor live-log)
идёт и в терминал, и в ``on_line`` — построчно по ``\\n``.
"""

from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TextIO


class _StreamTee(TextIO):
    """Обёртка TextIO: пишет в оригинал и уведомляет ``on_line`` по строкам."""

    def __init__(
        self,
        original: TextIO,
        on_line: Callable[[str], None],
        *,
        owner_thread_id: int,
    ) -> None:
        self._original = original
        self._on_line = on_line
        self._owner_thread_id = owner_thread_id
        self._pending = ""

    def write(self, s: str, /) -> int:
        if not s:
            return 0
        _ = self._original.write(s)
        self._original.flush()
        self._pending += s
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._on_line(line + "\n")
        return len(s)

    def flush(self) -> None:
        self._original.flush()
        if self._pending:
            self._on_line(self._pending)
            self._pending = ""

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return self._original.isatty()

    @property
    def encoding(self) -> str:
        return self._original.encoding

    @property
    def errors(self) -> str | None:
        return self._original.errors


@contextmanager
def stream_process_output(
    on_line: Callable[[str], None] | None,
) -> Iterator[None]:
    """Контекст: дублировать stdout/stderr процесса в ``on_line`` (если задан).

    Args:
        on_line: Callback на каждую завершённую строку (с ``\\n``), кроме хвоста
            без перевода строки — он уходит на ``flush`` или при выходе из контекста.

    Yields:
        None.
    """
    if on_line is None:
        yield
        return
    owner = threading.get_ident()
    out_tee = _StreamTee(sys.stdout, on_line, owner_thread_id=owner)
    err_tee = _StreamTee(sys.stderr, on_line, owner_thread_id=owner)
    prev_out, prev_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_tee, err_tee  # type: ignore[assignment]
    try:
        yield
    finally:
        out_tee.flush()
        err_tee.flush()
        sys.stdout, sys.stderr = prev_out, prev_err


def format_exception_for_log(exc: BaseException) -> str:
    """Текст traceback для live-log (как в терминале при необ необработанном исключении)."""
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def emit_console_line(line: str) -> None:
    """Печать строки repair-hop в stderr сервера (терминал Owner, вне SSE).

    Args:
        line: Текст; при отсутствии ``\\n`` в конце перевод строки добавляется.
    """
    if not line:
        return
    if not line.endswith("\n"):
        line = f"{line}\n"
    sys.stderr.write(line)
    sys.stderr.flush()
