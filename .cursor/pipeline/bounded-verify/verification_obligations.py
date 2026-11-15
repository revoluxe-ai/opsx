# -*- coding: utf-8 -*-
"""Декларация verification obligations (mandatory / conditional / advisory).

Не дублирует registry pack HOW. Enforcement: compose + archive verify.
GRAPH — advisory routing only; этот модуль — machine policy source.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class ObligationKind(str, Enum):
    MANDATORY = "MANDATORY"
    CONDITIONAL = "CONDITIONAL"
    ADVISORY = "ADVISORY"


@dataclass(frozen=True)
class VerificationObligation:
    """Одна проверка в политике verification plan."""

    obligation_id: str
    kind: ObligationKind
    registry_check_id: str | None
    evidence_contract: str
    policy_source: str


# Registry-backed static checks (HOW from registry.yaml).
_STATIC_REGISTRY_IDS: tuple[str, ...] = (
    "pyright-strict",
    "mypy",
    "pytest-targeted",
    "pytest-unit",
    "pytest-integration",
    "pytest-destructive",
)

# Mandatory non-registry obligations (require adapters / separate executors).
_MANDATORY_ADAPTER_OBLIGATIONS: tuple[VerificationObligation, ...] = (
    VerificationObligation(
        obligation_id="static-check-coverage",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="STATIC_CHECK_COVERAGE_REPORT",
        policy_source=".cursor/commands/opsx-static-check-coverage.md",
    ),
    VerificationObligation(
        obligation_id="external-call-coverage",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="EXTERNAL_CALL_COVERAGE_REPORT",
        policy_source=".cursor/commands/opsx-external-call-coverage.md",
    ),
    VerificationObligation(
        obligation_id="scenario-test-coverage",
        kind=ObligationKind.CONDITIONAL,
        registry_check_id=None,
        evidence_contract="SCENARIO_TEST_COVERAGE_REPORT",
        policy_source=".cursor/pipeline/contracts/SCENARIO_TEST_COVERAGE.md",
    ),
    VerificationObligation(
        obligation_id="performance-review",
        kind=ObligationKind.CONDITIONAL,
        registry_check_id=None,
        evidence_contract="PERFORMANCE_REVIEW_REPORT",
        policy_source="openspec/changes/<id>/performance-review/TRIGGER.json",
    ),
    VerificationObligation(
        obligation_id="antipattern-gate",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="ANTIPATTERN_GATE_REPORT",
        policy_source=".cursor/commands/opsx-antipattern-gate.md",
    ),
    VerificationObligation(
        obligation_id="static-check-synthesis",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="STATIC_CHECK_FULL_SYNTHESIS",
        policy_source=".cursor/commands/opsx-static-check.md",
    ),
    VerificationObligation(
        obligation_id="impl-verify",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="IMPL_VERIFY_RECEIPT",
        policy_source=".cursor/pipeline/contracts/VERIFY_PHASES.md",
    ),
    VerificationObligation(
        obligation_id="archive-verify",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="ARCHIVE_VERIFY_GO",
        policy_source="openspec/policies/VERIFY_POLICY.md",
    ),
)


def _static_ids_for_profile(profile: str) -> tuple[str, ...]:
    if profile == "bounded_minimal":
        return ("pyright-strict", "mypy", "pytest-targeted")
    if profile == "static_fast":
        return ("pyright-strict", "mypy", "pytest-targeted", "pytest-unit", "pytest-integration")
    if profile in ("static_full", "full_required"):
        return _STATIC_REGISTRY_IDS
    raise ValueError(f"unknown profile: {profile}")


def _static_registry_obligations(profile: str) -> tuple[VerificationObligation, ...]:
    """Registry-only obligations for static profiles (not full_required)."""
    static_ids = _static_ids_for_profile(profile)
    return tuple(
        VerificationObligation(
            obligation_id=cid,
            kind=ObligationKind.MANDATORY,
            registry_check_id=cid,
            evidence_contract="REGISTRY_WORKER_PACKET",
            policy_source=".cursor/static-check/registry.yaml",
        )
        for cid in static_ids
    )


def obligations_for_profile(profile: str) -> tuple[VerificationObligation, ...]:
    """Возвращает obligations для compose profile."""
    if profile == "full_required":
        return _static_registry_obligations("static_full") + _MANDATORY_ADAPTER_OBLIGATIONS
    if profile in ("static_fast", "static_full", "bounded_minimal"):
        return _static_registry_obligations(profile)
    raise ValueError(f"unknown profile: {profile}")


def frontier_adapter_obligations(profile: str) -> tuple[VerificationObligation, ...]:
    """Mandatory adapter obligations that must be in bounded plan frontier (excludes pre/post gates)."""
    _PRE_POST = frozenset({"impl-verify", "archive-verify"})
    return tuple(
        o
        for o in obligations_for_profile(profile)
        if o.kind == ObligationKind.MANDATORY
        and o.registry_check_id is None
        and o.obligation_id not in _PRE_POST
    )


def unsupported_mandatory_obligations(
    profile: str,
) -> tuple[VerificationObligation, ...]:
    """Obligations без registry_check_id — plan cannot freeze until adapted."""
    from pathlib import Path

    from bounded_verify_s5_adapters import adapter_implemented

    root = Path(__file__).resolve().parents[3]
    return tuple(
        o
        for o in frontier_adapter_obligations(profile)
        if not adapter_implemented(o.obligation_id, root=root)
    )


def registry_check_ids_for_profile(profile: str) -> tuple[str, ...]:
    """Только check_id, допустимые в bounded plan.required_checks today."""
    return tuple(
        o.registry_check_id
        for o in obligations_for_profile(profile)
        if o.registry_check_id
    )
