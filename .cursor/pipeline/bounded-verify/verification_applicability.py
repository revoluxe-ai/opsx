# -*- coding: utf-8 -*-
"""Applicability matrix для verification obligations (S5 semantics).

Отделяет pre-verification (impl), in-plan frontier, post-verification (archive),
и conditional checks с явным applicability result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pathlib import Path

from verification_obligations import ObligationKind, VerificationObligation, obligations_for_profile

REPO_ROOT = Path(__file__).resolve().parents[3]

_S5_OBLIGATION_IDS = frozenset(
    {
        "static-check-coverage",
        "pyright-strict-coverage",  # legacy alias
        "external-call-coverage",
        "scenario-test-coverage",
        "antipattern-gate",
        "static-check-synthesis",
    }
)


class ApplicabilityZone(str, Enum):
    PRE_VERIFICATION = "PRE_VERIFICATION"
    IN_PLAN_FRONTIER = "IN_PLAN_FRONTIER"
    POST_VERIFICATION = "POST_VERIFICATION"
    ADVISORY_ONLY = "ADVISORY_ONLY"


class ApplicabilityResult(str, Enum):
    MANDATORY_IN_PLAN = "MANDATORY_IN_PLAN"
    MANDATORY_PRE_GATE = "MANDATORY_PRE_GATE"
    MANDATORY_POST_GATE = "MANDATORY_POST_GATE"
    CONDITIONAL_WHEN_TRIGGERED = "CONDITIONAL_WHEN_TRIGGERED"
    ADVISORY = "ADVISORY"
    UNSUPPORTED_FAIL_CLOSED = "UNSUPPORTED_FAIL_CLOSED"


@dataclass(frozen=True)
class ObligationApplicability:
    obligation_id: str
    zone: ApplicabilityZone
    result: ApplicabilityResult
    native_adapter: str | None
    notes: str


_OBLIGATION_ZONE: dict[str, tuple[ApplicabilityZone, ApplicabilityResult, str | None, str]] = {
    "pyright-strict": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:pyright-strict",
        "Typecheck via static-check registry worker",
    ),
    "mypy": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:mypy",
        "Mypy via registry",
    ),
    "pytest-targeted": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:pytest-targeted",
        "Targeted pytest wave",
    ),
    "pytest-unit": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:pytest-unit",
        "Unit pytest wave",
    ),
    "pytest-integration": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:pytest-integration",
        "Integration pytest wave",
    ),
    "pytest-destructive": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.MANDATORY_IN_PLAN,
        "registry:pytest-destructive",
        "Destructive pytest — separate execution authorization",
    ),
    "static-check-coverage": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
        None,
        "Changed-code static-check scope audit; legacy id pyright-strict-coverage",
    ),
    "pyright-strict-coverage": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
        None,
        "Legacy alias of static-check-coverage",
    ),
    "external-call-coverage": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
        None,
        "External call coverage report adapter required",
    ),
    "scenario-test-coverage": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.CONDITIONAL_WHEN_TRIGGERED,
        None,
        "Applicable when scenario specs exist; else N/A",
    ),
    "antipattern-gate": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
        None,
        "Antipattern gate adapter required",
    ),
    "static-check-synthesis": (
        ApplicabilityZone.IN_PLAN_FRONTIER,
        ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
        None,
        "FULL static-check synthesis adapter required",
    ),
    "performance-review": (
        ApplicabilityZone.ADVISORY_ONLY,
        ApplicabilityResult.CONDITIONAL_WHEN_TRIGGERED,
        None,
        "Trigger-based; not a registry static substitute",
    ),
    "impl-verify": (
        ApplicabilityZone.PRE_VERIFICATION,
        ApplicabilityResult.MANDATORY_PRE_GATE,
        "opsx-verify:mode=impl",
        "IMPL_OK prerequisite; not inside frozen plan frontier",
    ),
    "archive-verify": (
        ApplicabilityZone.POST_VERIFICATION,
        ApplicabilityResult.MANDATORY_POST_GATE,
        "opsx-verify:mode=archive",
        "Independent archive gate; never inside its own prerequisite workflow",
    ),
}


def applicability_for_obligation(obligation: VerificationObligation) -> ObligationApplicability:
    """Возвращает applicability row для одной obligation."""
    row = _OBLIGATION_ZONE.get(obligation.obligation_id)
    if row is None:
        return ObligationApplicability(
            obligation_id=obligation.obligation_id,
            zone=ApplicabilityZone.IN_PLAN_FRONTIER,
            result=ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED,
            native_adapter=None,
            notes="unknown obligation",
        )
    zone, result, adapter, notes = row
    if obligation.kind == ObligationKind.ADVISORY:
        result = ApplicabilityResult.ADVISORY
    if obligation.obligation_id in _S5_OBLIGATION_IDS:
        from bounded_verify_s5_adapters import adapter_capability_status, adapter_implemented

        if adapter_implemented(obligation.obligation_id, root=REPO_ROOT):
            result = ApplicabilityResult.MANDATORY_IN_PLAN
            adapter = f"s5:{obligation.obligation_id}"
        else:
            result = ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED
            adapter = None
            notes = (
                f"{notes}; capability={adapter_capability_status(obligation.obligation_id, root=REPO_ROOT).value}"
            )
    return ObligationApplicability(
        obligation_id=obligation.obligation_id,
        zone=zone,
        result=result,
        native_adapter=adapter,
        notes=notes,
    )


def applicability_matrix(profile: str) -> tuple[ObligationApplicability, ...]:
    """Полная матрица для compose profile."""
    return tuple(applicability_for_obligation(o) for o in obligations_for_profile(profile))


def unsupported_applicable_obligations(profile: str) -> tuple[ObligationApplicability, ...]:
    """Obligations, которые applicable, но без native adapter (fail-closed)."""
    return tuple(
        row
        for row in applicability_matrix(profile)
        if row.result == ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED
    )
