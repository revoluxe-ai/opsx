# -*- coding: utf-8 -*-
"""Deterministic S5 conditional obligation applicability (compose/freeze)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_plan import change_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

_SCENARIO_HEADING_RE = re.compile(r"###\s+\d+\.\s+`Scenario:\s*([^`]+)`")


class ApplicabilityDisposition(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MANDATORY_APPLICABLE = "MANDATORY_APPLICABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ConditionalApplicability:
    obligation_id: str
    disposition: ApplicabilityDisposition
    evidence_path: str | None
    notes: str


def _scenario_plan_path(change_id: str, *, root: Path) -> Path:
    return change_dir(change_id, root=root) / "scenario-coverage" / "plan.md"


def resolve_scenario_test_coverage_applicability(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> ConditionalApplicability:
    """Scenario obligation: applicable when durable scenario plan defines scenarios."""
    plan_path = _scenario_plan_path(change_id, root=root)
    if not plan_path.is_file():
        specs = change_dir(change_id, root=root) / "specs"
        if specs.is_dir() and any(specs.rglob("*.md")):
            return ConditionalApplicability(
                obligation_id="scenario-test-coverage",
                disposition=ApplicabilityDisposition.UNKNOWN,
                evidence_path=str(specs),
                notes="specs present without scenario-coverage/plan.md",
            )
        return ConditionalApplicability(
            obligation_id="scenario-test-coverage",
            disposition=ApplicabilityDisposition.NOT_APPLICABLE,
            evidence_path=None,
            notes="no scenario-coverage plan or specs",
        )
    text = plan_path.read_text(encoding="utf-8")
    scenarios = _SCENARIO_HEADING_RE.findall(text)
    if not scenarios:
        return ConditionalApplicability(
            obligation_id="scenario-test-coverage",
            disposition=ApplicabilityDisposition.UNKNOWN,
            evidence_path=str(plan_path),
            notes="scenario plan present but no parsed scenarios",
        )
    return ConditionalApplicability(
        obligation_id="scenario-test-coverage",
        disposition=ApplicabilityDisposition.MANDATORY_APPLICABLE,
        evidence_path=str(plan_path),
        notes=f"scenarios={len(scenarios)}",
    )


def resolve_performance_review_applicability(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> ConditionalApplicability:
    """Performance review: applicable when change records an explicit trigger receipt."""
    trigger = change_dir(change_id, root=root) / "performance-review" / "TRIGGER.json"
    if not trigger.is_file():
        return ConditionalApplicability(
            obligation_id="performance-review",
            disposition=ApplicabilityDisposition.NOT_APPLICABLE,
            evidence_path=None,
            notes="no performance-review/TRIGGER.json",
        )
    return ConditionalApplicability(
        obligation_id="performance-review",
        disposition=ApplicabilityDisposition.MANDATORY_APPLICABLE,
        evidence_path=str(trigger),
        notes="trigger receipt present",
    )


def assert_full_required_conditional_coverage(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> None:
    """Applicable conditional + unsupported adapter → PLAN_COVERAGE_INCOMPLETE."""
    from bounded_verify_plan import BoundedVerifyPlanError
    from bounded_verify_s5_adapters import adapter_implemented

    checks = (
        resolve_scenario_test_coverage_applicability(change_id, root=root),
        resolve_performance_review_applicability(change_id, root=root),
    )
    for row in checks:
        if row.disposition == ApplicabilityDisposition.UNKNOWN:
            raise BoundedVerifyPlanError(
                "PLAN_COVERAGE_INCOMPLETE",
                f"conditional applicability UNKNOWN for {row.obligation_id}: {row.notes}",
            )
        if row.disposition == ApplicabilityDisposition.MANDATORY_APPLICABLE:
            if not adapter_implemented(row.obligation_id, root=root):
                raise BoundedVerifyPlanError(
                    "PLAN_COVERAGE_INCOMPLETE",
                    f"applicable conditional {row.obligation_id} lacks machine-ready adapter",
                )


def assert_plan_required_checks_match_conditional_policy(
    change_id: str,
    check_ids: tuple[str, ...],
    *,
    root: Path = REPO_ROOT,
) -> None:
    """Applicable conditional obligations must be in plan; unsupported → fail-closed."""
    from bounded_verify_plan import BoundedVerifyPlanError
    from bounded_verify_s5_adapters import adapter_implemented

    assert_full_required_conditional_coverage(change_id, root=root)
    for resolver in (
        resolve_scenario_test_coverage_applicability,
        resolve_performance_review_applicability,
    ):
        row = resolver(change_id, root=root)
        if row.disposition == ApplicabilityDisposition.MANDATORY_APPLICABLE:
            if row.obligation_id not in check_ids:
                raise BoundedVerifyPlanError(
                    "PLAN_COVERAGE_INCOMPLETE",
                    f"applicable conditional {row.obligation_id} missing from required_checks",
                )
            if not adapter_implemented(row.obligation_id, root=root):
                raise BoundedVerifyPlanError(
                    "PLAN_COVERAGE_INCOMPLETE",
                    f"applicable conditional {row.obligation_id} lacks machine-ready adapter",
                )


def conditional_applicability_input_paths(change_id: str, *, root: Path = REPO_ROOT) -> tuple[Path, ...]:
    """Durable inputs that drive conditional applicability decisions."""
    cdir = change_dir(change_id, root=root)
    return (
        cdir / "scenario-coverage" / "plan.md",
        cdir / "performance-review" / "TRIGGER.json",
    )


def collect_conditional_inputs_digest(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, str]:
    """Content identity for conditional applicability inputs (present or tombstone)."""
    import hashlib

    rows: dict[str, str] = {}
    for path in conditional_applicability_input_paths(change_id, root=root):
        rel = str(path.relative_to(root)) if path.is_file() else str(path)
        if path.is_file():
            rows[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            rows[rel] = "TOMBSTONE"
    policy_paths = (
        ".cursor/pipeline/bounded-verify/bounded_verify_s5_obligation_applicability.py",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
    )
    for rel in policy_paths:
        p = root / rel
        if p.is_file():
            rows[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return rows


def applicability_decisions_snapshot(change_id: str, *, root: Path = REPO_ROOT) -> list[dict[str, str]]:
    """Current applicability decisions for policy binding."""
    rows: list[dict[str, str]] = []
    for resolver in (
        resolve_scenario_test_coverage_applicability,
        resolve_performance_review_applicability,
    ):
        row = resolver(change_id, root=root)
        rows.append(
            {
                "obligation_id": row.obligation_id,
                "disposition": row.disposition.value,
                "notes": row.notes,
            }
        )
    return rows


def assert_conditional_applicability_current(
    plan: Mapping[str, Any],
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> None:
    """Frozen applicability must match live inputs; post-freeze changes → BLOCKED."""
    from bounded_verify_plan import BoundedVerifyPlanError
    from bounded_verify_snapshot import PLAN_SCHEMA_V2

    if str(plan.get("schema_version", "")) != PLAN_SCHEMA_V2:
        return
    policy = plan.get("verification_policy")
    if not isinstance(policy, dict):
        raise BoundedVerifyPlanError("PLAN_INVALID", "verification_policy missing")
    frozen = policy.get("applicability_decisions")
    if not isinstance(frozen, list):
        raise BoundedVerifyPlanError("PLAN_INVALID", "applicability_decisions missing")
    live = applicability_decisions_snapshot(change_id, root=root)
    if frozen != live:
        raise BoundedVerifyPlanError(
            "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
            "conditional applicability decisions differ from frozen plan",
        )
    frozen_digest = policy.get("conditional_inputs_digest")
    live_digest = collect_conditional_inputs_digest(change_id, root=root)
    if isinstance(frozen_digest, dict) and frozen_digest != live_digest:
        raise BoundedVerifyPlanError(
            "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
            "conditional applicability input digest mismatch",
        )


def applicable_conditional_check_ids(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
) -> tuple[str, ...]:
    """Conditional obligations that must appear in frozen full_required plan."""
    from bounded_verify_s5_adapters import adapter_implemented

    out: list[str] = []
    for resolver in (
        resolve_scenario_test_coverage_applicability,
        resolve_performance_review_applicability,
    ):
        row = resolver(change_id, root=root)
        if row.disposition == ApplicabilityDisposition.MANDATORY_APPLICABLE:
            if adapter_implemented(row.obligation_id, root=root):
                out.append(row.obligation_id)
    return tuple(out)
