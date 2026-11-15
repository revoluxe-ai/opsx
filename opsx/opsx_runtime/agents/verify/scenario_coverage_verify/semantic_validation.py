"""Semantic scenario review acceptance criteria, independent of LLM-audit scheduling.

Changes to this module invalidate scenario reviewer policy, while changes to
transport/concurrency/graph orchestration do not invalidate product evidence.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.verify.scenario_coverage_verify.evidence_contract import review_proof_error
from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageVerifyAnswer


_SCENARIO_ROW = re.compile(r"^\s*-\s*SCENARIO_ID:\s*(.+?)\s*$", re.MULTILINE)


def _audit_completeness_error(
    answer: ScenarioCoverageVerifyAnswer,
    plan_bundle: dict[str, Any],
) -> str | None:
    """Fail closed unless every frozen scenario was reviewed at this plan identity."""
    if answer.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN":
        return None
    declared = plan_bundle.get("scenario_ids")
    if isinstance(declared, list) and declared:
        expected = [str(item) for item in declared]
    else:
        plan = str(plan_bundle.get("plan_text") or "")
        expected = _SCENARIO_ROW.findall(plan)
    receipt = plan_bundle.get("plan_receipt") or {}
    evidence_id = str(receipt.get("PLAN_EVIDENCE_ID") or "")
    if not expected or len(expected) != len(set(expected)):
        return "AUDIT_INCOMPLETE: frozen plan scenario rows missing or duplicated"
    if not evidence_id:
        return "AUDIT_INCOMPLETE: frozen plan PLAN_EVIDENCE_ID missing"
    if answer.plan_evidence_id and answer.plan_evidence_id != evidence_id:
        return "AUDIT_STALE: plan_evidence_id does not match frozen receipt"
    actual = [row.scenario_id for row in answer.row_reviews]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        return (
            f"AUDIT_INCOMPLETE: reviewed {len(actual)}/{len(expected)} scenario rows; "
            f"missing={missing[:5]}; extra={extra[:5]}; order/identity must match frozen plan"
        )
    if answer.verdict == "SCENARIO_TEST_COVERAGE_PASS" and any(
        row.assessment != "SUPPORTED" for row in answer.row_reviews
    ):
        return "AUDIT_INCOMPLETE: PASS contains non-SUPPORTED row assessment"
    return None


def _audit_proof_error(
    answer: ScenarioCoverageVerifyAnswer,
    *,
    repo_root: Path,
    change_id: str,
    source_snapshot: dict[str, Any],
    mappings: dict[str, str],
) -> str | None:
    """Deterministic check, not a claim of complete semantic proof."""
    if answer.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN":
        return None
    bound_digest = str(source_snapshot.get("source_digest") or "")
    if answer.source_digest and bound_digest and answer.source_digest != bound_digest:
        return "AUDIT_STALE: stored answer digest mismatches hop source snapshot"
    for row in answer.row_reviews:
        if row.scenario_id not in mappings:
            return f"AUDIT_MAPPING_MISSING: {row.scenario_id}"
        if (
            row.test_node_id != mappings[row.scenario_id]
            and (row.assessment == "SUPPORTED" or bool(row.test_node_id))
        ):
            return f"AUDIT_MAPPING_CHANGED: {row.scenario_id}"
        # GAP may intentionally report an absent mapped test as empty.
        # A missing test must remain actionable FAIL, not be hidden as UNKNOWN.
        if row.assessment == "SUPPORTED":
            problem = review_proof_error(
                repo_root, change_id, row, expected_node=mappings[row.scenario_id],
            )
            if problem:
                return f"AUDIT_PROOF_INVALID: {problem}"
    return None



