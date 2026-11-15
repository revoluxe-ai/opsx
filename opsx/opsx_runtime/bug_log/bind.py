"""Привязка журнала к агенту без смены каждого конструктора."""

from __future__ import annotations

from typing import TypeVar

from bug_log.null_journal import NullBugJournal
from bug_log.port import BugJournal

T = TypeVar("T")


def bind_bug_journal(agent: T, journal: BugJournal | None) -> T:
    """Положить журнал на агент как ``_bug_journal``.

    Args:
        agent: Экземпляр агента или узла.
        journal: Журнал прогона; None заменяется на NullBugJournal.

    Returns:
        Тот же объект.
    """
    setattr(agent, "_bug_journal", journal if journal is not None else NullBugJournal())
    return agent
