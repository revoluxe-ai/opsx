"""Журнал-заглушка для unit-тестов и незакрытой проводки."""

from __future__ import annotations

from bug_log.ids import BugRef, bug_id_from_fingerprint
from bug_log.fingerprint import fingerprint_for
from bug_log.schemas import BugCandidate, BugObservation, BugSummary, JournalHealth


class NullBugJournal:
    """No-op реализация. Live-прогон с ней запрещён guard-ом composition root."""

    def __init__(self) -> None:
        self.recorded: list[BugObservation] = []
        self.registered: list[BugCandidate] = []

    async def register(self, candidate: BugCandidate) -> BugRef:
        self.registered.append(candidate)
        fp = fingerprint_for(
            lane=candidate.lane,
            observation=candidate.observation,
            location=candidate.location,
            tool=candidate.tool,
        )
        return BugRef(
            bug_id=bug_id_from_fingerprint(fp),
            local_id=candidate.local_id,
            change_id=None,
        )

    async def record(self, observation: BugObservation) -> None:
        self.recorded.append(observation)

    async def link(
        self,
        source: BugRef,
        target: BugRef,
        relation: str,
        evidence: tuple[str, ...] = (),
    ) -> None:
        await self.record(
            BugObservation(
                bug_id=source.bug_id,
                kind="LINKED",
                summary=relation,
                related_bug_id=target.bug_id,
                relation=relation,
                evidence_refs=list(evidence),
            )
        )

    def resolve(self, bug_id: str) -> BugSummary | None:
        return None

    def health(self) -> JournalHealth:
        return JournalHealth(events=len(self.recorded) + len(self.registered))
