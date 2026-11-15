# -*- coding: utf-8 -*-
"""S5 verification adapters — capability-gated, no synthetic PASS."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from bounded_verify_s5_common import s5_hermetic_adapters_enabled
from bounded_verify_s5_pyright_coverage import (
    pyright_audit_ready,
    run_pyright_strict_coverage_adapter,
)
from verification_obligations import frontier_adapter_obligations

from bounded_verify_s5_runners import (
    run_antipattern_gate_adapter,
    run_external_call_coverage_adapter,
    run_performance_review_adapter,
    run_scenario_test_coverage_adapter,
    run_static_check_synthesis_adapter,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

AdapterRunner = Callable[[str, Path], tuple[str, Mapping[str, Any]]]


class AdapterCapability(str, Enum):
    """Executable support — registration alone is never READY."""

    UNSUPPORTED = "UNSUPPORTED"
    READY = "READY"


@dataclass(frozen=True)
class S5AdapterSpec:
    """One frontier obligation adapter."""

    obligation_id: str
    evidence_contract: str
    capability_for_root: Callable[[Path], AdapterCapability]
    runner: AdapterRunner | None


def _unsupported_runner(obligation_id: str, _change_id: str, _root: Path) -> tuple[str, Mapping[str, Any]]:
    return "EXECUTION_ERROR", {
        "obligation_id": obligation_id,
        "code": "ADAPTER_UNSUPPORTED",
        "message": "native adapter not machine-ready for this obligation",
    }


def _pyright_capability(root: Path) -> AdapterCapability:
    if not pyright_audit_ready(root):
        return AdapterCapability.UNSUPPORTED
    return AdapterCapability.READY


def _always_unsupported(_root: Path) -> AdapterCapability:
    return AdapterCapability.UNSUPPORTED


def _hermetic_ready(_root: Path) -> AdapterCapability:
    if s5_hermetic_adapters_enabled(root=_root):
        return AdapterCapability.READY
    return AdapterCapability.UNSUPPORTED


_S5_ADAPTERS: tuple[S5AdapterSpec, ...] = (
    S5AdapterSpec(
        "static-check-coverage",
        "STATIC_CHECK_COVERAGE_REPORT",
        _pyright_capability,
        run_pyright_strict_coverage_adapter,
    ),
    S5AdapterSpec(
        "external-call-coverage",
        "EXTERNAL_CALL_COVERAGE_REPORT",
        _hermetic_ready,
        run_external_call_coverage_adapter,
    ),
    S5AdapterSpec(
        "scenario-test-coverage",
        "SCENARIO_TEST_COVERAGE_REPORT",
        _hermetic_ready,
        run_scenario_test_coverage_adapter,
    ),
    S5AdapterSpec(
        "antipattern-gate",
        "ANTIPATTERN_GATE_REPORT",
        _hermetic_ready,
        run_antipattern_gate_adapter,
    ),
    S5AdapterSpec(
        "static-check-synthesis",
        "STATIC_CHECK_FULL_SYNTHESIS",
        _hermetic_ready,
        run_static_check_synthesis_adapter,
    ),
    S5AdapterSpec(
        "performance-review",
        "PERFORMANCE_REVIEW_REPORT",
        _hermetic_ready,
        run_performance_review_adapter,
    ),
)

_ADAPTER_BY_ID: dict[str, S5AdapterSpec] = {a.obligation_id: a for a in _S5_ADAPTERS}
_ADAPTER_BY_ID["pyright-strict-coverage"] = _ADAPTER_BY_ID["static-check-coverage"]

MANDATORY_S5_OBLIGATION_IDS: frozenset[str] = frozenset(
    o.obligation_id for o in frontier_adapter_obligations("full_required")
)


def adapter_capability_status(
    obligation_id: str,
    *,
    root: Path = REPO_ROOT,
) -> AdapterCapability:
    """Current machine capability for obligation at repo root."""
    spec = _ADAPTER_BY_ID.get(obligation_id)
    if spec is None:
        return AdapterCapability.UNSUPPORTED
    return spec.capability_for_root(root)


def adapter_implemented(obligation_id: str, *, root: Path = REPO_ROOT) -> bool:
    """True only when adapter is READY (verified semantics), not merely registered."""
    return adapter_capability_status(obligation_id, root=root) == AdapterCapability.READY


def frontier_adapter_check_ids(*, root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Mandatory S5 adapter obligations that are READY (excludes conditional)."""
    mandatory = MANDATORY_S5_OBLIGATION_IDS
    return tuple(
        a.obligation_id
        for a in _S5_ADAPTERS
        if a.obligation_id in mandatory
        and adapter_capability_status(a.obligation_id, root=root) == AdapterCapability.READY
    )


def run_s5_adapter(
    check_id: str,
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> tuple[str, Mapping[str, Any]]:
    """Dispatch S5 adapter; never synthesize PASS for UNSUPPORTED obligations."""
    spec = _ADAPTER_BY_ID.get(check_id)
    if spec is None:
        return "EXECUTION_ERROR", {"code": "ADAPTER_UNKNOWN", "check_id": check_id}
    if adapter_capability_status(check_id, root=root) != AdapterCapability.READY:
        return _unsupported_runner(check_id, change_id, root)
    if spec.runner is None:
        return _unsupported_runner(check_id, change_id, root)
    return spec.runner(change_id, root)


def assert_no_unsupported_mandatory_adapters(
    *,
    root: Path = REPO_ROOT,
) -> None:
    """Fail-closed guard for full_required frontier."""
    from bounded_verify_plan import BoundedVerifyPlanError

    missing = [
        oid
        for oid in sorted(MANDATORY_S5_OBLIGATION_IDS)
        if not adapter_implemented(oid, root=root)
    ]
    if missing:
        raise BoundedVerifyPlanError(
            "PLAN_COVERAGE_INCOMPLETE",
            f"mandatory S5 adapters not machine-ready: {', '.join(sorted(missing))}",
        )
