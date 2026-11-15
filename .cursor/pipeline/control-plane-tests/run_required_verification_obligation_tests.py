# -*- coding: utf-8 -*-
"""Acceptance tests for D:S:008 required verification obligation evaluator.

Machine-checkable CASE_1..CASE_8 per transfer-v0/DS008/ACCEPTANCE_PLAN.json.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALUATOR_PATH = (
    REPO_ROOT / ".cursor" / "pipeline" / "control-plane" / "verification_obligation.py"
)
SYNTHESIS_PATH = REPO_ROOT / ".cursor" / "static-check" / "contracts" / "synthesis.md"
VERIFY_POLICY_PATH = REPO_ROOT / "openspec" / "policies" / "VERIFY_POLICY.md"
ACCEPTANCE_PLAN_PATH = Path(
    r"C:\Users\vkita\my_code\opsx-reconciliation\target-source-v0-001"
    r"\transfer-v0\DS008\ACCEPTANCE_PLAN.json"
)

sys.path.insert(0, str(EVALUATOR_PATH.parent))
from verification_obligation import (  # noqa: E402
    ObligationEvaluation,
    evaluate_required_obligations,
)

Result = tuple[str, bool, str]


@dataclass
class Case:
    case_id: str
    fn: Callable[[], tuple[bool, str]]


def _valid_pass_packet(check_id: str) -> dict[str, Any]:
    return {
        "CHECK_ID": check_id,
        "COMMAND": f"poetry run {check_id}",
        "EXIT_CODE": 0,
        "STATUS": "PASS",
        "FINDINGS_TOTAL": 0,
        "FINDINGS_TRUNCATED": "no",
        "FINDINGS": [],
        "RAW_TAIL": "n/a",
        "ERROR": "n/a",
    }


def _expand_packets(
    worker_packets: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for entry in worker_packets:
        if "packet_ref" in entry:
            check_id = str(entry["check_id"])
            expanded.append(
                {"check_id": check_id, "packet": _valid_pass_packet(check_id)}
            )
            continue
        expanded.append(dict(entry))
    return expanded


def _run_fixture(fixture: Mapping[str, Any]) -> ObligationEvaluation:
    packets = _expand_packets(list(fixture["worker_packets"]))
    return evaluate_required_obligations(
        context=dict(fixture["context"]),
        declared_obligations=list(fixture["declared_obligations"]),
        scheduled_checks=list(fixture["scheduled_checks"]),
        worker_packets=packets,
    )


def _unsatisfied_map(evaluation: ObligationEvaluation) -> dict[str, str]:
    return {
        item.check_id: item.outcome for item in evaluation.unsatisfied_required
    }


def _outcome_map(evaluation: ObligationEvaluation) -> dict[str, str]:
    return {item.check_id: item.outcome for item in evaluation.outcomes}


def _case_1() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "pyright-strict", "requirement": "REQUIRED"},
            {"check_id": "mypy", "requirement": "REQUIRED"},
            {"check_id": "pytest-targeted", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict", "mypy", "pytest-targeted"],
        "worker_packets": [
            {"check_id": cid, "packet": _valid_pass_packet(cid)}
            for cid in ("pyright-strict", "mypy", "pytest-targeted")
        ],
    }
    ev = _run_fixture(fixture)
    ok = (
        ev.aggregate_pass_allowed
        and ev.obligation_verdict == "SATISFIED"
        and len(ev.unsatisfied_required) == 0
        and all(
            _outcome_map(ev)[cid] == "SATISFIED_REQUIRED"
            for cid in ("pyright-strict", "mypy", "pytest-targeted")
        )
    )
    return ok, f"verdict={ev.obligation_verdict}"


def _case_2() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "mypy", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict", "mypy"],
        "worker_packets": [
            {"check_id": "pyright-strict", "packet": _valid_pass_packet("pyright-strict")},
            {
                "check_id": "mypy",
                "packet": {
                    "CHECK_ID": "mypy",
                    "COMMAND": "poetry run mypy",
                    "EXIT_CODE": "n/a",
                    "STATUS": "BLOCKED",
                    "FINDINGS_TOTAL": 0,
                    "FINDINGS_TRUNCATED": "no",
                    "FINDINGS": [],
                    "RAW_TAIL": "poetry: command not found",
                    "ERROR": "missing poetry/config",
                },
            },
        ],
    }
    ev = _run_fixture(fixture)
    unsat = _unsatisfied_map(ev)
    ok = (
        not ev.aggregate_pass_allowed
        and ev.obligation_verdict == "UNSATISFIED"
        and unsat.get("mypy") == "FAIL_CLOSED"
    )
    return ok, f"unsatisfied={unsat}"


def _case_3() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "pytest-targeted", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict", "mypy", "pytest-targeted"],
        "worker_packets": [
            {"check_id": "pyright-strict", "packet": _valid_pass_packet("pyright-strict")},
            {"check_id": "mypy", "packet": _valid_pass_packet("mypy")},
        ],
    }
    ev = _run_fixture(fixture)
    unsat = _unsatisfied_map(ev)
    ok = (
        not ev.aggregate_pass_allowed
        and unsat.get("pytest-targeted") == "REQUIRED_NOT_EXECUTED"
    )
    return ok, f"unsatisfied={unsat}"


def _case_4() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "pyright-strict", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict"],
        "worker_packets": [
            {
                "check_id": "pyright-strict",
                "packet": {
                    "CHECK_ID": "pyright-strict",
                    "STATUS": "PASS",
                    "FINDINGS_TOTAL": 0,
                },
            }
        ],
    }
    ev = _run_fixture(fixture)
    unsat = _unsatisfied_map(ev)
    ok = (
        not ev.aggregate_pass_allowed
        and unsat.get("pyright-strict") == "FAIL_CLOSED"
    )
    return ok, f"unsatisfied={unsat}"


def _case_5() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "npm-run-build", "requirement": "UNAVAILABLE"},
            {"check_id": "pyright-strict", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict", "mypy"],
        "worker_packets": [
            {"check_id": "pyright-strict", "packet": _valid_pass_packet("pyright-strict")},
        ],
    }
    ev = _run_fixture(fixture)
    outcomes = _outcome_map(ev)
    ok = (
        ev.aggregate_pass_allowed
        and outcomes.get("npm-run-build") == "NOT_APPLICABLE"
        and "npm-run-build" not in _unsatisfied_map(ev)
    )
    return ok, f"outcomes={outcomes}"


def _case_6() -> tuple[bool, str]:
    checks = [
        "pyright-strict",
        "mypy",
        "pytest-targeted",
        "pytest-unit",
        "pytest-integration",
        "pytest-destructive",
    ]
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": cid, "requirement": "REQUIRED"} for cid in checks
        ]
        + [{"check_id": "npm-run-build", "requirement": "UNAVAILABLE"}],
        "scheduled_checks": checks,
        "worker_packets": [
            {"check_id": cid, "packet_ref": "valid_pass_packet_template"}
            for cid in checks
        ],
    }
    ev = _run_fixture(fixture)
    ok = ev.aggregate_pass_allowed and ev.obligation_verdict == "SATISFIED"
    return ok, f"verdict={ev.obligation_verdict}"


def _case_7() -> tuple[bool, str]:
    checks = [
        "pyright-strict",
        "mypy",
        "pytest-targeted",
        "pytest-unit",
        "pytest-integration",
    ]
    fixture = {
        "context": {"mode": "FAST", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "pytest-destructive", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": checks,
        "worker_packets": [
            {"check_id": cid, "packet_ref": "valid_pass_packet_template"}
            for cid in checks
        ],
    }
    ev = _run_fixture(fixture)
    unsat = _unsatisfied_map(ev)
    ok = (
        not ev.aggregate_pass_allowed
        and unsat.get("pytest-destructive") == "DECLARATION_SCHEDULE_DIVERGENCE"
    )
    return ok, f"unsatisfied={unsat}"


def _case_8() -> tuple[bool, str]:
    fixture = {
        "context": {"mode": "FULL", "adapter_id": "poetry-python"},
        "declared_obligations": [
            {"check_id": "npm-run-build", "requirement": "UNAVAILABLE"},
            {"check_id": "pyright-strict", "requirement": "REQUIRED"},
        ],
        "scheduled_checks": ["pyright-strict", "mypy", "npm-run-build"],
        "worker_packets": [
            {"check_id": "pyright-strict", "packet": _valid_pass_packet("pyright-strict")},
            {"check_id": "npm-run-build", "packet": _valid_pass_packet("npm-run-build")},
        ],
    }
    ev = _run_fixture(fixture)
    outcomes = _outcome_map(ev)
    ok = (
        ev.aggregate_pass_allowed
        and outcomes.get("npm-run-build") == "NOT_APPLICABLE"
        and "npm-run-build" not in _unsatisfied_map(ev)
    )
    return ok, f"outcomes={outcomes}"


def _probe_synthesis_integration() -> tuple[bool, str]:
    text = SYNTHESIS_PATH.read_text(encoding="utf-8")
    ok = (
        "verification_obligation" in text
        and "evaluate_required_obligations" in text
        and "aggregate_pass_allowed" in text
        and "MUST NOT be `PASS`" in text or "MUST NOT be PASS" in text
    )
    return ok, "synthesis references evaluator before PASS"


def _probe_no_evidence_present_fixtures() -> tuple[bool, str]:
    if ACCEPTANCE_PLAN_PATH.exists():
        plan_text = ACCEPTANCE_PLAN_PATH.read_text(encoding="utf-8")
    else:
        plan_text = ""
    ok = '"evidence_present"' not in plan_text
    return ok, "acceptance plan has no evidence_present keys"


def _probe_verify_policy_no_second_evaluator() -> tuple[bool, str]:
    if not VERIFY_POLICY_PATH.exists():
        return True, "VERIFY_POLICY absent (optional path unchanged)"
    text = VERIFY_POLICY_PATH.read_text(encoding="utf-8")
    ok = "evaluate_required_obligations" not in text
    return ok, "VERIFY_POLICY does not reimplement evaluator"


def build_cases() -> list[Case]:
    return [
        Case("CASE_1", _case_1),
        Case("CASE_2", _case_2),
        Case("CASE_3", _case_3),
        Case("CASE_4", _case_4),
        Case("CASE_5", _case_5),
        Case("CASE_6", _case_6),
        Case("CASE_7", _case_7),
        Case("CASE_8", _case_8),
        Case("PROBE_SYNTHESIS", _probe_synthesis_integration),
        Case("PROBE_NO_EVIDENCE_PRESENT", _probe_no_evidence_present_fixtures),
        Case("PROBE_VERIFY_POLICY", _probe_verify_policy_no_second_evaluator),
    ]


def main() -> int:
    results: list[Result] = []
    for case in build_cases():
        ok, detail = case.fn()
        results.append((case.case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"REQUIRED_VERIFICATION_OBLIGATION_TESTS: {passed}/{len(results)} passed")
    for cid, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {cid}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
