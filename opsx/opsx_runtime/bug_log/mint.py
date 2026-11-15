"""Точки выдачи bug_id из gate findings, review и repair queue."""

from __future__ import annotations

from typing import Any

from bug_log.port import BugJournal
from bug_log.schemas import BugCandidate, BugObservation, Provenance


def candidate_from_gate_line(
    *,
    local_id: str,
    tool: str,
    observation: str,
) -> BugCandidate:
    """Кандидат из строки quality gate."""
    return BugCandidate(
        lane="verify_gate",
        observation=observation,
        location=observation[:160],
        tool=tool,
        local_id=local_id,
        alias_scheme=tool,
        detail={"gate_code": tool},
    )


async def register_gate_findings(
    journal: BugJournal | None,
    findings: list[Any],
) -> dict[str, str]:
    """Зарегистрировать findings gates. Вернуть local_id → bug_id.

    Args:
        journal: Журнал прогона или None.
        findings: Объекты с finding_id, source_tool, observation.
    """
    if journal is None:
        return {}
    mapping: dict[str, str] = {}
    for finding in findings:
        local_id = str(getattr(finding, "finding_id", "") or "")
        observation = str(getattr(finding, "observation", "") or "").strip()
        if not local_id or not observation:
            continue
        tool = str(getattr(finding, "source_tool", "") or "pytest")
        ref = await journal.register(
            candidate_from_gate_line(local_id=local_id, tool=tool, observation=observation)
        )
        mapping[local_id] = ref.bug_id
        bug_id_attr = getattr(finding, "bug_id", None)
        if bug_id_attr is not None or hasattr(finding, "bug_id"):
            try:
                finding.bug_id = ref.bug_id  # type: ignore[attr-defined]
            except Exception:
                pass
    return mapping


async def register_text_finding(
    journal: BugJournal | None,
    *,
    lane: str,
    local_id: str | None,
    location: str,
    text: str,
    alias_scheme: str | None = None,
) -> str | None:
    """Зарегистрировать текстовый finding (bugbot, owner, hunt)."""
    if journal is None or not text.strip():
        return None
    ref = await journal.register(
        BugCandidate(
            lane=lane,
            observation=text.strip(),
            location=location,
            local_id=local_id,
            alias_scheme=alias_scheme or lane,
        )
    )
    return ref.bug_id


async def record_hop(
    journal: BugJournal | None,
    *,
    bug_id: str | None,
    summary: str,
    status: str | None = None,
    role: str | None = None,
    hop: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Записать HOP_RESULT, если известен bug_id."""
    if journal is None or not bug_id:
        return
    await journal.record(
        BugObservation(
            bug_id=bug_id,
            kind="HOP_RESULT",
            summary=summary,
            status=status,
            provenance=Provenance(agent_role=role, hop=hop, lane=role or "hop"),
            detail=detail or {},
        )
    )
