# -*- coding: utf-8 -*-
"""S5 native evidence inventory + read-only receipt validators (no new writers)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]

OBLIGATION_EVIDENCE_CONTRACTS: dict[str, str] = {
    "external-call-coverage": "EXTERNAL_CALL_COVERAGE_REPORT",
    "antipattern-gate": "ANTIPATTERN_GATE_REPORT",
    "static-check-synthesis": "STATIC_CHECK_FULL_SYNTHESIS",
    "static-check-coverage": "STATIC_CHECK_COVERAGE_REPORT",
    "pyright-strict-coverage": "PYRIGHT_STRICT_COVERAGE_REPORT",
    "scenario-test-coverage": "SCENARIO_TEST_COVERAGE_REPORT",
    "performance-review": "PERFORMANCE_REVIEW_REPORT",
}


class GateAcceptance(str, Enum):
    """Gate acceptance — never PASS for FAIL/BLOCKED/UNKNOWN outcomes."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class S5CommandInventoryRow:
    """One agent-facing verification command mapped to machine evidence."""

    obligation_id: str
    command_contract: str
    native_runner: str | None
    durable_evidence: bool
    freshness_inputs: tuple[str, ...]
    authorized_tier: str
    receipt_writer_required: bool
    adapter_ready: bool
    minimal_adapter: str


def s5_command_inventory(*, root: Path = REPO_ROOT) -> tuple[S5CommandInventoryRow, ...]:
    """Authoritative inventory for S5 obligations (read-only)."""
    from bounded_verify_s5_adapters import adapter_implemented

    rows = (
        S5CommandInventoryRow(
            "external-call-coverage",
            ".cursor/commands/opsx-external-call-coverage.md",
            None,
            False,
            ("change_id", "product_paths", "policy_revision"),
            "agent-only",
            True,
            adapter_implemented("external-call-coverage", root=root),
            "OWNER_DECISION: native CLI + receipt writer",
        ),
        S5CommandInventoryRow(
            "antipattern-gate",
            ".cursor/commands/opsx-antipattern-gate.md",
            None,
            False,
            ("change_id", "policy_revision"),
            "agent-only",
            True,
            adapter_implemented("antipattern-gate", root=root),
            "OWNER_DECISION: deterministic gate runner",
        ),
        S5CommandInventoryRow(
            "static-check-synthesis",
            ".cursor/commands/opsx-static-check.md",
            None,
            False,
            ("registry_revision", "mode=FULL"),
            "agent-only",
            True,
            adapter_implemented("static-check-synthesis", root=root),
            "OWNER_DECISION: static-check orchestrator",
        ),
        S5CommandInventoryRow(
            "static-check-coverage",
            ".cursor/commands/opsx-static-check-coverage.md",
            "bounded_verify_s5_pyright_coverage.audit_pyright_strict_coverage",
            True,
            ("merge_base", "pyrightconfig", "worktree_paths"),
            "deterministic-local",
            False,
            adapter_implemented("static-check-coverage", root=root),
            "bounded evidence publication only",
        ),
        S5CommandInventoryRow(
            "scenario-test-coverage",
            ".cursor/pipeline/contracts/SCENARIO_TEST_COVERAGE.md",
            None,
            False,
            ("scenario-coverage/plan.md",),
            "agent-only",
            True,
            adapter_implemented("scenario-test-coverage", root=root),
            "OWNER_DECISION: scenario certify CLI",
        ),
        S5CommandInventoryRow(
            "performance-review",
            "conditional",
            None,
            False,
            ("performance-review/TRIGGER.json",),
            "agent-only",
            True,
            adapter_implemented("performance-review", root=root),
            "OWNER_DECISION: performance review adapter",
        ),
    )
    return rows


def _expected_contract(obligation_id: str) -> str | None:
    return OBLIGATION_EVIDENCE_CONTRACTS.get(obligation_id)


def validate_evidence_receipt_readonly(
    obligation_id: str,
    receipt_path: Path,
    *,
    root: Path = REPO_ROOT,
    expected_contract: str | None = None,
    bound_policy_identity: Mapping[str, str] | None = None,
    bound_input_digest: str | None = None,
    bound_execution_id: str | None = None,
) -> dict[str, Any]:
    """Read-only receipt validation with separated acceptance dimensions."""
    from bounded_verify_s5_adapters import adapter_implemented

    contract_required = expected_contract or _expected_contract(obligation_id)
    base: dict[str, Any] = {
        "obligation_id": obligation_id,
        "schema_valid": False,
        "gate_acceptance": GateAcceptance.BLOCKED.value,
        "execution_outcome": "UNKNOWN",
        "evidence_contract": "",
    }

    if not receipt_path.is_file():
        base["reason"] = "RECEIPT_MISSING"
        return base
    try:
        doc = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        base["reason"] = "RECEIPT_INVALID_JSON"
        return base
    if not isinstance(doc, Mapping):
        base["reason"] = "RECEIPT_NOT_OBJECT"
        return base

    from bounded_verify_s5_fixture_semantics import is_production_s5_evidence

    if not is_production_s5_evidence(doc):
        base["reason"] = "FIXTURE_EVIDENCE_NOT_PRODUCTION"
        base["gate_acceptance"] = GateAcceptance.REJECTED.value
        return base

    if not adapter_implemented(obligation_id, root=root):
        base["gate_acceptance"] = GateAcceptance.UNSUPPORTED.value
        base["reason"] = "ADAPTER_UNSUPPORTED"
        return base

    contract = str(doc.get("evidence_contract", ""))
    base["evidence_contract"] = contract
    status = str(doc.get("STATUS", ""))
    base["execution_outcome"] = status if status else "UNKNOWN"

    schema_ok = bool(contract) and status in ("PASS", "FAIL", "BLOCKED")
    if contract_required and contract != contract_required:
        base["reason"] = "CONTRACT_MISMATCH"
        return base
    if not schema_ok:
        base["reason"] = "SCHEMA_INVALID"
        return base
    base["schema_valid"] = True

    if bound_policy_identity is not None:
        receipt_policy = doc.get("policy_identity")
        if not isinstance(receipt_policy, Mapping):
            base["reason"] = "POLICY_IDENTITY_MISSING"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base
        for key, expected in bound_policy_identity.items():
            if str(receipt_policy.get(key, "")) != str(expected):
                base["reason"] = "POLICY_IDENTITY_MISMATCH"
                base["gate_acceptance"] = GateAcceptance.REJECTED.value
                return base

    if bound_input_digest is not None:
        if str(doc.get("input_digest", "")) != bound_input_digest:
            base["reason"] = "INPUT_STALE"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base

    if bound_execution_id is not None:
        if str(doc.get("execution_id", "")) != bound_execution_id:
            base["reason"] = "EXECUTION_MISMATCH"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base

    required_pass_fields = (
        "plan_id",
        "execution_id",
        "input_digest",
        "policy_identity",
        "evidence_producer",
        "check_evidence",
    )
    if status == "PASS":
        missing = [f for f in required_pass_fields if f not in doc]
        if missing:
            base["reason"] = f"PASS_MISSING_FIELDS:{','.join(missing)}"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base
        proof = doc.get("check_evidence")
        if not isinstance(proof, Mapping) or not proof:
            base["reason"] = "FORGED_PASS_NO_CHECK_EVIDENCE"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base
        from bounded_verify_s5_common import canonical_evidence_producer

        producer = str(doc.get("evidence_producer", ""))
        if producer != canonical_evidence_producer(obligation_id):
            base["reason"] = "EVIDENCE_PRODUCER_MISMATCH"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base
        if bound_policy_identity is None or bound_input_digest is None or bound_execution_id is None:
            base["reason"] = "IDENTITY_BINDINGS_REQUIRED"
            base["gate_acceptance"] = GateAcceptance.REJECTED.value
            return base
        base["gate_acceptance"] = GateAcceptance.ACCEPTED.value
        base["reason"] = "GATE_ACCEPTED"
        return base

    if status == "FAIL":
        base["gate_acceptance"] = GateAcceptance.REJECTED.value
        base["reason"] = "EXECUTION_FAIL"
        return base

    base["gate_acceptance"] = GateAcceptance.BLOCKED.value
    base["reason"] = "EXECUTION_BLOCKED"
    return base


def validate_fixture_evidence_receipt_readonly(
    obligation_id: str,
    receipt_path: Path,
    *,
    bound_policy_identity: Mapping[str, str] | None = None,
    bound_input_digest: str | None = None,
    bound_execution_id: str | None = None,
) -> dict[str, Any]:
    """Validate S5-A fixture receipts (never production gate ACCEPTED)."""
    from bounded_verify_s5_fixture_semantics import (
        FIXTURE_STATUS_PASS,
        is_production_s5_evidence,
    )

    base: dict[str, Any] = {
        "obligation_id": obligation_id,
        "schema_valid": False,
        "gate_acceptance": GateAcceptance.REJECTED.value,
        "execution_outcome": "UNKNOWN",
    }
    if not receipt_path.is_file():
        base["reason"] = "RECEIPT_MISSING"
        return base
    doc = json.loads(receipt_path.read_text(encoding="utf-8"))
    if is_production_s5_evidence(doc):
        base["reason"] = "NOT_FIXTURE_EVIDENCE"
        return base
    if str(doc.get("STATUS", "")) != FIXTURE_STATUS_PASS:
        base["reason"] = "FIXTURE_NOT_PASS"
        base["execution_outcome"] = str(doc.get("STATUS", ""))
        return base
    base["schema_valid"] = True
    if bound_execution_id and str(doc.get("execution_id", "")) != bound_execution_id:
        base["reason"] = "EXECUTION_MISMATCH"
        return base
    if bound_input_digest and str(doc.get("input_digest", "")) != bound_input_digest:
        base["reason"] = "INPUT_STALE"
        return base
    base["gate_acceptance"] = GateAcceptance.ACCEPTED.value
    base["reason"] = "FIXTURE_TEST_ACCEPTED"
    return base


def owner_decision_requests() -> Sequence[dict[str, str]]:
    """Authority gaps requiring Owner approval before READY."""
    return (
        {
            "obligation_id": "external-call-coverage",
            "proposed_permission": "write openspec/changes/<id>/bounded-verify/receipts/external-call-coverage.json",
            "failure_semantics": "BLOCKED when receipt missing or stale",
        },
        {
            "obligation_id": "antipattern-gate",
            "proposed_permission": "invoke opsx-antipattern-gate with bounded write-set to receipts/",
            "failure_semantics": "FAIL_CLOSED on gate violation",
        },
        {
            "obligation_id": "static-check-synthesis",
            "proposed_permission": "orchestrate registry FULL wave with receipt persistence",
            "failure_semantics": "PLAN_COVERAGE_INCOMPLETE until receipt PASS",
        },
        {
            "obligation_id": "scenario-test-coverage",
            "proposed_permission": "scenario audit/certify CLI + receipt writer",
            "failure_semantics": "UNKNOWN applicability → BLOCKED",
        },
    )
