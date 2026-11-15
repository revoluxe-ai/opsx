"""Порт журнала багов. Реализации не импортируют агентов."""

from __future__ import annotations

from typing import Protocol

from bug_log.ids import BugRef
from bug_log.schemas import BugCandidate, BugObservation, BugSummary, JournalHealth


class BugJournal(Protocol):
    """Единственный владелец выдачи bug_id и записи событий."""

    async def register(self, candidate: BugCandidate) -> BugRef:
        """Выдать существующий или новый bug_id и записать sighting."""

    async def record(self, observation: BugObservation) -> None:
        """Дописать наблюдение по известному bug_id."""

    async def link(
        self,
        source: BugRef,
        target: BugRef,
        relation: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        """Связать два бага (same_root и аналоги)."""

    def resolve(self, bug_id: str) -> BugSummary | None:
        """Read-only снимок для входного пакета модели."""

    def health(self) -> JournalHealth:
        """Сколько событий записано, ушло в spool или потеряно."""
