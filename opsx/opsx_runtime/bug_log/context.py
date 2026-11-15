"""ContextVar журнала текущего прогона (дополнение к DI конструктора)."""

from __future__ import annotations

from contextvars import ContextVar, Token

from bug_log.port import BugJournal

current_journal: ContextVar[BugJournal | None] = ContextVar("opsx_bug_journal", default=None)


def bind_current_journal(journal: BugJournal | None) -> Token[BugJournal | None]:
    """Сделать журнал видимым узлам без явного аргумента."""
    return current_journal.set(journal)


def reset_current_journal(token: Token[BugJournal | None]) -> None:
    """Вернуть предыдущий журнал."""
    current_journal.reset(token)


def active_journal() -> BugJournal | None:
    """Журнал прогона или None."""
    return current_journal.get()
