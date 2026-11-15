# -*- coding: utf-8 -*-
"""S5-A deterministic adapter runners (hermetic fixtures only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_s5_common import s5_hermetic_adapters_enabled
from bounded_verify_s5_fixture_semantics import (
    FIXTURE_EVIDENCE_SCOPE,
    fixture_evidence_contract,
    fixture_result_status,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

_FIXTURES = REPO_ROOT / ".cursor/pipeline/bounded-verify-tests/fixtures/s5"


def _blocked(obligation_id: str, reason: str) -> tuple[str, Mapping[str, Any]]:
    return "EXECUTION_ERROR", {
        "obligation_id": obligation_id,
        "code": reason,
        "STATUS": "BLOCKED",
        "evidence_scope": FIXTURE_EVIDENCE_SCOPE,
        "production_evidence": False,
    }


def _hermetic_guard(change_id: str, obligation_id: str, root: Path) -> str | None:
    if not s5_hermetic_adapters_enabled(root=root):
        return "ADAPTER_UNSUPPORTED"
    return None


def _fixture_outcome(
    obligation_id: str,
    base_contract: str,
    ok: bool,
    check_evidence: Mapping[str, Any],
    *,
    blocked: bool = False,
) -> tuple[str, Mapping[str, Any]]:
    status = fixture_result_status(ok, blocked=blocked)
    packet: dict[str, Any] = {
        "obligation_id": obligation_id,
        "STATUS": status,
        "evidence_contract": fixture_evidence_contract(base_contract),
        "evidence_scope": FIXTURE_EVIDENCE_SCOPE,
        "production_evidence": False,
        "check_evidence": {
            **dict(check_evidence),
            "fixture_test_implementation_only": True,
        },
    }
    if status == fixture_result_status(True):
        return "INCOMPLETE", packet
    if status == fixture_result_status(False):
        return "FAIL", packet
    return "EXECUTION_ERROR", packet


def run_external_call_coverage_adapter(
    change_id: str,
    root: Path,
) -> tuple[str, Mapping[str, Any]]:
    obligation_id = "external-call-coverage"
    guard = _hermetic_guard(change_id, obligation_id, root)
    if guard:
        return _blocked(obligation_id, guard)
    manifest = _FIXTURES / "external-call-surfaces.json"
    if not manifest.is_file():
        return _blocked(obligation_id, "FIXTURE_MANIFEST_MISSING")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    surfaces = data.get("surfaces", [])
    product_root = root / "src/stroy"
    gaps: list[str] = []
    for item in surfaces:
        rel = str(item.get("path", ""))
        token = str(item.get("token", ""))
        test_rel = str(item.get("test_path", ""))
        src = product_root / rel if rel else None
        if src and src.is_file() and token in src.read_text(encoding="utf-8"):
            test_path = root / test_rel
            if not test_path.is_file():
                gaps.append(rel)
    if gaps:
        return _fixture_outcome(
            obligation_id,
            "EXTERNAL_CALL_COVERAGE_REPORT",
            False,
            {
                "fixture_inventory_only": True,
                "not_production_external_call_coverage": True,
                "uncovered_surfaces": gaps,
            },
        )
    return _fixture_outcome(
        obligation_id,
        "EXTERNAL_CALL_COVERAGE_REPORT",
        True,
        {
            "fixture_inventory_only": True,
            "not_production_external_call_coverage": True,
            "surfaces_audited": len(surfaces),
            "gaps": [],
        },
    )


def run_antipattern_gate_adapter(change_id: str, root: Path) -> tuple[str, Mapping[str, Any]]:
    obligation_id = "antipattern-gate"
    guard = _hermetic_guard(change_id, obligation_id, root)
    if guard:
        return _blocked(obligation_id, guard)
    rules_path = _FIXTURES / "antipattern-rules.json"
    if not rules_path.is_file():
        return _blocked(obligation_id, "ANTIPATTERN_RULES_MISSING")
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    forbidden = [str(x) for x in rules.get("forbidden_patterns", [])]
    hits: list[dict[str, str]] = []
    scan_root = root / "src/stroy"
    if scan_root.is_dir():
        for path in scan_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pat in forbidden:
                if re.search(pat, text):
                    hits.append({"path": str(path.relative_to(root)), "pattern": pat})
    if hits:
        return _fixture_outcome(
            obligation_id,
            "ANTIPATTERN_GATE_REPORT",
            False,
            {
                "fixture_regex_rules_only": True,
                "not_authoritative_antipattern_policy": True,
                "violations": hits,
            },
        )
    return _fixture_outcome(
        obligation_id,
        "ANTIPATTERN_GATE_REPORT",
        True,
        {
            "fixture_regex_rules_only": True,
            "not_authoritative_antipattern_policy": True,
            "violations": [],
            "rules_revision": rules.get("revision", ""),
        },
    )


def run_static_check_synthesis_adapter(
    change_id: str,
    root: Path,
) -> tuple[str, Mapping[str, Any]]:
    obligation_id = "static-check-synthesis"
    guard = _hermetic_guard(change_id, obligation_id, root)
    if guard:
        return _blocked(obligation_id, guard)
    from bounded_verify_registry_execution import (
        RegistryExecutionSupport,
        registry_check_support_matrix,
    )

    matrix = dict(registry_check_support_matrix(root=root))
    wave_a = ("pyright-strict", "mypy")
    for check_id in wave_a:
        if matrix.get(check_id) != RegistryExecutionSupport.MACHINE_EXECUTABLE.value:
            return _fixture_outcome(
                obligation_id,
                "STATIC_CHECK_FULL_SYNTHESIS",
                False,
                {
                    "certifies_full_required": False,
                    "fixture_matrix_audit_only": True,
                    "reason": "wave_A_not_machine_executable",
                    "matrix": matrix,
                },
            )
    if matrix.get("pytest-destructive") != RegistryExecutionSupport.UNSUPPORTED.value:
        return _fixture_outcome(
            obligation_id,
            "STATIC_CHECK_FULL_SYNTHESIS",
            False,
            {
                "certifies_full_required": False,
                "wave_D_must_remain_blocked": True,
                "matrix": matrix,
            },
        )
    return _fixture_outcome(
        obligation_id,
        "STATIC_CHECK_FULL_SYNTHESIS",
        True,
        {
            "certifies_full_required": False,
            "wave_D_status": "UNSUPPORTED_BY_POLICY",
            "fixture_matrix_audit_only": True,
            "wave_A": list(wave_a),
            "matrix": matrix,
        },
    )


def run_scenario_test_coverage_adapter(
    change_id: str,
    root: Path,
) -> tuple[str, Mapping[str, Any]]:
    obligation_id = "scenario-test-coverage"
    guard = _hermetic_guard(change_id, obligation_id, root)
    if guard:
        return _blocked(obligation_id, guard)
    plan_path = root / "openspec/changes" / change_id / "scenario-coverage" / "plan.md"
    if not plan_path.is_file():
        return _fixture_outcome(
            obligation_id,
            "SCENARIO_TEST_COVERAGE_REPORT",
            True,
            {
                "disposition": "NOT_APPLICABLE",
                "not_scenario_certification": True,
                "scenarios": 0,
            },
        )
    text = plan_path.read_text(encoding="utf-8")
    scenarios = re.findall(r"Scenario:\s*([^`\n]+)", text)
    missing_tests: list[str] = []
    for name in scenarios:
        slug = name.strip().lower().replace(" ", "-")
        test_path = root / "tests/integration" / f"test_scenario_{slug}.py"
        if not test_path.is_file():
            missing_tests.append(name.strip())
    if missing_tests:
        return _fixture_outcome(
            obligation_id,
            "SCENARIO_TEST_COVERAGE_REPORT",
            False,
            {
                "not_scenario_certification": True,
                "filename_match_only": True,
                "missing_tests": missing_tests,
            },
        )
    return _fixture_outcome(
        obligation_id,
        "SCENARIO_TEST_COVERAGE_REPORT",
        True,
        {
            "not_scenario_certification": True,
            "filename_match_only": True,
            "scenarios": scenarios,
        },
    )


def run_performance_review_adapter(change_id: str, root: Path) -> tuple[str, Mapping[str, Any]]:
    obligation_id = "performance-review"
    guard = _hermetic_guard(change_id, obligation_id, root)
    if guard:
        return _blocked(obligation_id, guard)
    trigger = root / "openspec/changes" / change_id / "performance-review" / "TRIGGER.json"
    if not trigger.is_file():
        return _fixture_outcome(
            obligation_id,
            "PERFORMANCE_REVIEW_REPORT",
            True,
            {
                "disposition": "NOT_APPLICABLE",
                "not_performance_review_acceptance": True,
            },
        )
    payload = json.loads(trigger.read_text(encoding="utf-8"))
    if not payload.get("triggered"):
        return _fixture_outcome(
            obligation_id,
            "PERFORMANCE_REVIEW_REPORT",
            True,
            {
                "disposition": "NOT_APPLICABLE",
                "not_performance_review_acceptance": True,
                "trigger": payload,
            },
        )
    review = root / "openspec/changes" / change_id / "performance-review" / "REVIEW.md"
    if not review.is_file():
        return _fixture_outcome(
            obligation_id,
            "PERFORMANCE_REVIEW_REPORT",
            False,
            {
                "not_performance_review_acceptance": True,
                "reason": "TRIGGERED_WITHOUT_REVIEW_ARTIFACT",
            },
            blocked=True,
        )
    return _fixture_outcome(
        obligation_id,
        "PERFORMANCE_REVIEW_REPORT",
        True,
        {
            "not_performance_review_acceptance": True,
            "review_file_present_fixture_only": True,
            "review_bytes": len(review.read_bytes()),
        },
    )


def adapter_runner_packet_with_identity(
    change_id: str,
    obligation_id: str,
    root: Path,
    packet: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Attach plan/input/policy identity for fixture receipt publication (S5-A)."""
    from bounded_verify_s5_common import (
        input_digest_for_change,
        policy_identity_for_change,
        require_frozen_plan_binding,
    )

    plan_id, plan_fp = require_frozen_plan_binding(change_id, root=root)
    enriched = dict(packet)
    enriched["plan_id"] = plan_id
    enriched["plan_fingerprint"] = plan_fp
    enriched["input_digest"] = input_digest_for_change(change_id, root=root)
    enriched["policy_identity"] = policy_identity_for_change(change_id, root=root)
    return enriched
