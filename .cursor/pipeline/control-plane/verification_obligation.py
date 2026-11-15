"""D:S:008 — единственный semantic evaluator required verification obligations.

Сливает declaration authority (quality-gates), scheduling (registry.yaml) и
native worker packets (worker-packet.md) в детерминированный obligation verdict.
Synthesis и archive consumers только потребляют результат; не дублировать логику.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

_VALID_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "ERROR"})
_REQUIRED_REQUIREMENTS = frozenset({"REQUIRED"})
_NOT_REQUIRED_REQUIREMENTS = frozenset(
    {"NOT_REQUIRED", "UNAVAILABLE", "PROJECT_NOT_APPLICABLE"}
)
_FAIL_CLOSED_OUTCOMES = frozenset(
    {
        "FAIL_CLOSED",
        "REQUIRED_NOT_EXECUTED",
        "DECLARATION_SCHEDULE_DIVERGENCE",
    }
)


@dataclass(frozen=True)
class ObligationCheckResult:
    """Per-check outcome от evaluate_required_obligations."""

    check_id: str
    outcome: str
    worker_status: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ObligationEvaluation:
    """Aggregate obligation verdict для synthesis / archive consumers."""

    obligation_verdict: str
    aggregate_pass_allowed: bool
    unsatisfied_required: tuple[ObligationCheckResult, ...] = field(default_factory=tuple)
    outcomes: tuple[ObligationCheckResult, ...] = field(default_factory=tuple)


def _normalize_worker_packets(
    worker_packets: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Приводит worker_packets к dict[check_id → native packet]."""
    if isinstance(worker_packets, Mapping):
        normalized: dict[str, Mapping[str, Any]] = {}
        for key, value in worker_packets.items():
            if "packet" in value and isinstance(value["packet"], Mapping):
                normalized[str(key)] = value["packet"]
            else:
                normalized[str(key)] = value
        return normalized

    normalized = {}
    for entry in worker_packets:
        check_id = str(entry["check_id"])
        packet = entry.get("packet")
        if isinstance(packet, Mapping):
            normalized[check_id] = packet
    return normalized


def _is_valid_native_packet(packet: Mapping[str, Any], check_id: str) -> bool:
    """Проверяет обязательные native fields worker-packet.md."""
    if not packet:
        return False
    if str(packet.get("CHECK_ID", "")) != check_id:
        return False
    status = packet.get("STATUS")
    if status not in _VALID_STATUSES:
        return False
    if "COMMAND" not in packet:
        return False
    if "EXIT_CODE" not in packet:
        return False
    if "FINDINGS_TOTAL" not in packet:
        return False
    if "FINDINGS" not in packet:
        return False
    if "ERROR" not in packet:
        return False
    return True


def _requirement_kind(requirement: str) -> str:
    upper = str(requirement).upper()
    if upper in _REQUIRED_REQUIREMENTS:
        return "REQUIRED"
    if upper in _NOT_REQUIRED_REQUIREMENTS:
        return "NOT_REQUIRED"
    return upper


def evaluate_required_obligations(
    context: Mapping[str, Any],
    declared_obligations: Sequence[Mapping[str, Any]],
    scheduled_checks: Sequence[str],
    worker_packets: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
) -> ObligationEvaluation:
    """Детерминированно оценивает required verification obligations.

    Args:
        context: Активный verification context (mode, adapter_id, …).
        declared_obligations: Нормализованные declarations из quality-gates.
        scheduled_checks: Check ids из registry.yaml для active MODE.
        worker_packets: Native worker packets keyed by check_id.

    Returns:
        ObligationEvaluation с aggregate_pass_allowed и per-check outcomes.
    """
    _ = context  # reserved for future adapter specialization; not inferred today
    schedule = {str(check_id) for check_id in scheduled_checks}
    packets = _normalize_worker_packets(worker_packets)

    outcomes: list[ObligationCheckResult] = []
    unsatisfied: list[ObligationCheckResult] = []

    for obligation in declared_obligations:
        check_id = str(obligation["check_id"])
        requirement = _requirement_kind(str(obligation.get("requirement", "")))

        if requirement != "REQUIRED":
            outcomes.append(
                ObligationCheckResult(
                    check_id=check_id,
                    outcome="NOT_APPLICABLE",
                    reason="declaration_not_required",
                )
            )
            continue

        if check_id not in schedule:
            result = ObligationCheckResult(
                check_id=check_id,
                outcome="DECLARATION_SCHEDULE_DIVERGENCE",
                reason="required_but_not_scheduled",
            )
            outcomes.append(result)
            unsatisfied.append(result)
            continue

        packet = packets.get(check_id)
        if packet is None:
            result = ObligationCheckResult(
                check_id=check_id,
                outcome="REQUIRED_NOT_EXECUTED",
                reason="packet_absent",
            )
            outcomes.append(result)
            unsatisfied.append(result)
            continue

        if not _is_valid_native_packet(packet, check_id):
            result = ObligationCheckResult(
                check_id=check_id,
                outcome="FAIL_CLOSED",
                reason="invalid_native_packet",
                worker_status=str(packet.get("STATUS", "n/a")),
            )
            outcomes.append(result)
            unsatisfied.append(result)
            continue

        status = str(packet["STATUS"])
        if status == "PASS":
            outcomes.append(
                ObligationCheckResult(
                    check_id=check_id,
                    outcome="SATISFIED_REQUIRED",
                    worker_status=status,
                )
            )
            continue

        result = ObligationCheckResult(
            check_id=check_id,
            outcome="FAIL_CLOSED",
            worker_status=status,
            reason="non_pass_worker_status",
        )
        outcomes.append(result)
        unsatisfied.append(result)

    aggregate_pass_allowed = len(unsatisfied) == 0
    obligation_verdict = "SATISFIED" if aggregate_pass_allowed else "UNSATISFIED"
    return ObligationEvaluation(
        obligation_verdict=obligation_verdict,
        aggregate_pass_allowed=aggregate_pass_allowed,
        unsatisfied_required=tuple(unsatisfied),
        outcomes=tuple(outcomes),
    )


def forbid_pass_when_unsatisfied(evaluation: ObligationEvaluation) -> bool:
    """True когда synthesis MUST NOT emit Final STATUS=PASS."""
    return not evaluation.aggregate_pass_allowed
