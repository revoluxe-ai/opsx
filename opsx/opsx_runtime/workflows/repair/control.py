"""Pure transition/control helpers for the LangGraph Bug Team.

No adapter calls and no filesystem mutation live here. These functions are
independently testable primitives used by the graph nodes.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping

from .schemas import (
    ClassificationPayload,
    FanInBranch,
    Finding,
    FindingOutcome,
    FindingReport,
    HuntCandidate,
    RelationPayload,
    RepairState,
)


def stable_finding_id(
    finding: str,
    location: str,
    *,
    prefix: str = "F",
) -> str:
    digest = hashlib.sha256(
        f"{location}\n{finding}".encode("utf-8")
    ).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def materialize_review(
    review: FindingReport | Mapping[str, Any],
) -> list[Finding]:
    """Validate Bugbot input first, then materialize a deterministic queue."""
    report = (
        review
        if isinstance(review, FindingReport)
        else FindingReport.model_validate(review)
    )
    if report.status in {"CLEAN", "NO_DIFF"}:
        return []
    if report.status == "BLOCKED":
        raise ValueError(report.block_reason or "Bugbot review blocked")

    queue: list[Finding] = []
    seen: set[str] = set()
    for raw in report.findings:
        finding_id = raw.id or stable_finding_id(
            raw.finding,
            raw.location,
            prefix="F-ROOT",
        )
        if finding_id in seen:
            continue
        seen.add(finding_id)
        queue.append(
            Finding(
                id=finding_id,
                bug_id=raw.bug_id,
                finding=raw.finding,
                location=raw.location,
                severity=raw.severity,
                root_relation=raw.root_relation,
                evidence_paths=raw.evidence_paths,
            )
        )
    return queue


def claim_next(state: RepairState) -> tuple[Finding | None, list[Finding]]:
    """Claim the first not-yet-seen finding, preserving deterministic order."""
    for index, finding in enumerate(state.queue):
        if finding.id not in state.seen:
            remaining = state.queue[:index] + state.queue[index + 1 :]
            return finding, remaining
    return None, list(state.queue)


def classification_route(
    classification: ClassificationPayload,
) -> str:
    if classification.status == "BLOCKED":
        return "blocked"
    return {
        "IMPLEMENTATION_BUG": "repair",
        "NEW_REQUIREMENT": "opsx_new",
        "ARCHIVED_PLANNING_BUG": "archived_delta",
        "SPEC_BUG": "opsx_update",
        "SPEC_GAP": "opsx_update",
        "FALSE_POSITIVE": "reject",
        "OUT_OF_SCOPE": "out_of_scope",
        "INCONCLUSIVE": "blocked",
    }[classification.kind]


def red_route(status: str) -> str:
    if status == "RED_CONFIRMED":
        return "owner"
    if status in {"PASS_ALREADY", "TEST_NOT_REPRODUCING"}:
        return "not_reproducing"
    return "blocked"


def green_route(status: str) -> str:
    return "invariant" if status == "GREEN_CONFIRMED" else "blocked"


def relation_to_finding(
    candidate: HuntCandidate,
    relation: RelationPayload,
) -> Finding | None:
    if relation.relation != "SAME_INVARIANT_FINDING":
        return None
    location = candidate.location or candidate.affected_component
    return Finding(
        id=candidate.hunt_candidate_id,
        finding=candidate.possible_invariant_violation,
        location=location,
        severity=candidate.severity,
        root_relation="SAME_INVARIANT_FINDING",
        evidence_paths=candidate.evidence_paths,
    )


def merge_same_invariant_candidates(
    state: RepairState,
    candidates: Iterable[tuple[HuntCandidate, RelationPayload]],
) -> list[Finding]:
    existing = {finding.id for finding in state.queue}
    existing.update(state.seen)
    merged = list(state.queue)
    for candidate, relation in candidates:
        finding = relation_to_finding(candidate, relation)
        if finding is None or finding.id in existing:
            continue
        merged.append(finding)
        existing.add(finding.id)
    return merged


def blocking_relations(relations: Iterable[RelationPayload]) -> list[str]:
    return [
        relation.hunt_candidate_id
        for relation in relations
        if relation.blocks_family_closure
    ]


def branch_summary(outcomes: Iterable[FindingOutcome]) -> list[FanInBranch]:
    rows: list[FanInBranch] = []
    for outcome in outcomes:
        rows.append(
            FanInBranch(
                finding_id=outcome.finding.id,
                terminal_state=outcome.terminal_state,
                red_status=outcome.red.status if outcome.red else None,
                green_status=outcome.green.status if outcome.green else None,
                patch_hash=outcome.fix.patch_hash if outcome.fix else None,
            )
        )
    return rows


def can_fanin(state: RepairState) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    if state.active is not None:
        blockers.append(state.active.id)
    if any(finding.id not in state.seen for finding in state.queue):
        blockers.extend(
            finding.id
            for finding in state.queue
            if finding.id not in state.seen
        )
    blockers.extend(
        relation.hunt_candidate_id
        for relation in state.relations
        if relation.blocks_family_closure
        and relation.hunt_candidate_id not in state.seen
    )
    blockers = list(dict.fromkeys(blockers))
    return not blockers, blockers


def aggregate_reported_input_tokens(state: RepairState) -> int:
    total = 0
    for run in state.runs:
        if run.input_tokens_reported is not None:
            total += run.input_tokens_reported
    return total


def enforce_mission_token_budget(state: RepairState) -> str | None:
    observed = aggregate_reported_input_tokens(state)
    if observed > state.max_tokens:
        return (
            f"mission input-token accounting exceeded max_tokens: "
            f"{observed}>{state.max_tokens}"
        )
    return None



def family_convergence(state: RepairState) -> tuple[bool, list[str]]:
    """Fail-closed family closure gate before FAN_IN_ASSEMBLER."""
    ready, blockers = can_fanin(state)
    issues = list(blockers)
    if state.invariants:
        if not state.hunts:
            issues.append("FINAL_INVARIANT_HUNT_MISSING")
        elif state.hunts[-1].hunt_result != "CLEAR":
            issues.append(
                f"FINAL_INVARIANT_HUNT_{state.hunts[-1].hunt_result}"
            )
    for outcome in state.finished:
        if outcome.fix is not None:
            if outcome.green is None or outcome.green.status != "GREEN_CONFIRMED":
                issues.append(f"{outcome.finding.id}:GREEN_NOT_CONFIRMED")
    issues = list(dict.fromkeys(issues))
    return ready and not issues, issues


def had_code_repair(state: RepairState) -> bool:
    """Был ли в миссии успешный FIX хотя бы по одному finding."""
    if any(outcome.terminal_state == "FIXED" for outcome in state.finished):
        return True
    if state.fix is not None and state.fix.status == "FIXED":
        if state.commit_sha or str(state.commit_status or "") not in {"", "PENDING"}:
            return True
    return False


def can_post_complete_rehunt(state: RepairState) -> bool:
    """После CLOSE с FIXED: ещё один bounded hunt (лимит ``max_search_rounds``).

    Если последний hunt уже **CLEAR** и verification-круг уже был
    (``search_round > 0``) — дальше не гоняем. Новый круг только после
    новой починки (последний hunt не CLEAR, например CANDIDATES → fix → CLOSE).
    """
    if state.error:
        return False
    if not str(state.status).startswith("COMPLETE_"):
        return False
    if not had_code_repair(state):
        return False
    if not state.invariants:
        return False
    if state.search_round >= state.max_search_rounds:
        return False
    if state.hunts and state.hunts[-1].hunt_result == "CLEAR":
        if state.search_round > 0:
            return False
    return True


def post_complete_rehunt_update(state: RepairState) -> dict[str, Any]:
    """Сброс fan-in и переход на следующий verification hunt."""
    next_round = state.search_round + 1
    return {
        "search_round": next_round,
        "fanin": None,
        "status": "POST_COMPLETE_REHUNT",
        "current_stage": "hunt",
        "error": None,
    }
