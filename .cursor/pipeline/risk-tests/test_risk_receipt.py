"""Focused positive and fail-closed tests for the OPSX risk receipt helper."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HELPER = (
    Path(__file__).resolve().parents[1] / "risk" / "risk_receipt.py"
)
CHANGE_ID = "bounded-risk"


def _write_json(path: Path, value: object) -> None:
    """Write deterministic JSON fixture content."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _planning_classification(profile: str = "STANDARD") -> dict[str, Any]:
    """Return a complete planning classifier payload."""

    return {
        "risk_profile": profile,
        "risk_triggers": ["Owner contract changes shared scheduler ownership."],
        "planning_lenses": ["Resolve one durable scheduler write authority."],
        "owner_attention_requirements": ["Surface the selected owner boundary."],
        "unresolved_risk_questions": ["none"],
        "escalation_conditions": ["Re-run if scheduler ownership changes."],
        "rationale": "Shared ownership needs explicit planning scrutiny.",
        "evidence": ["owner-contract.md: shared scheduler boundary"],
    }


def _tasking_classification(profile: str = "LOW") -> dict[str, Any]:
    """Return a complete tasking classifier payload without check selection."""

    return {
        "risk_profile": profile,
        "risk_triggers": ["Chosen adapter is isolated behind one interface."],
        "tasking_lenses": ["Keep the adapter boundary explicit."],
        "implementation_boundaries": ["Only the selected adapter owns writes."],
        "sequencing_constraints": ["Introduce the adapter before callers migrate."],
        "task_split_constraints": ["Keep caller migration separate from cleanup."],
        "must_not_combine": ["Do not combine unrelated session refactoring."],
        "prerequisite_constraints": ["Design ownership remains authoritative."],
        "owner_attention_requirements": ["Surface the bounded adapter migration."],
        "unresolved_implementation_questions": ["none"],
        "escalation_conditions": ["Re-run if another write adapter is selected."],
        "rationale": "The chosen solution is bounded despite planning uncertainty.",
        "evidence": ["design.md: selected adapter and write owner"],
    }


def _change(tmp_path: Path) -> Path:
    """Create the minimum Change inputs shared by helper tests."""

    change = tmp_path / "openspec" / "changes" / CHANGE_ID
    change.mkdir(parents=True)
    _ = (change / "owner-contract.md").write_text(
        "# Owner Contract\nThe scheduler has one write owner.\n",
        encoding="utf-8",
    )
    return change


def _run(
    change: Path,
    mode: str,
    classification: dict[str, Any],
    *,
    scenario_evidence: str = "not-required",
    escalation: str = "clear",
) -> subprocess.CompletedProcess[str]:
    """Invoke the public helper CLI for one test classification."""

    payload = change / f"{mode}-classification.json"
    _write_json(payload, classification)
    command = [
        sys.executable,
        str(HELPER),
        mode,
        "--change-id",
        CHANGE_ID,
        "--change-root",
        str(change),
        "--classification",
        str(payload),
    ]
    if mode == "planning":
        command.extend(
            [
                "--ocr-review-state",
                "PASS",
                "--ocr-review-change-id",
                CHANGE_ID,
                "--ocr-reviewed-artifact",
                str(change / "owner-contract.md"),
                "--ocr-evidence",
                "same-turn-pasted-pass",
            ]
        )
    else:
        command.extend(
            [
                "--planning-escalation-status",
                escalation,
                "--scenario-evidence",
                scenario_evidence,
            ]
        )
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _prepare_solution(change: Path) -> None:
    """Create chosen design and delta spec inputs for tasking mode."""

    _ = (change / "proposal.md").write_text("# Proposal\nBounded adapter.\n")
    _ = (change / "design.md").write_text(
        "# Design\nThe selected adapter exclusively owns writes.\n"
    )
    spec = change / "specs" / "risk" / "spec.md"
    spec.parent.mkdir(parents=True)
    _ = spec.write_text("# Risk spec\n## Requirement\nOne write owner.\n")
    decision = change / "decisions" / "adapter" / "decision.md"
    decision.parent.mkdir(parents=True)
    _ = decision.write_text("# Decision\nUse the selected adapter.\n")


def _lf_hash(path: Path) -> str:
    """Compute the scenario contract's LF-normalized content hash."""

    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(content).hexdigest()


def _prepare_scenario_pair(change: Path) -> tuple[Path, Path]:
    """Create a current durable scenario plan pair."""

    scenario_root = change / "scenario-coverage"
    scenario_root.mkdir()
    plan = scenario_root / "plan.md"
    _ = plan.write_text("# Scenario plan\nPLAN_STATUS: ready\n")
    receipt = scenario_root / "plan.receipt.json"
    _write_json(
        receipt,
        {
            "SCHEMA_VERSION": 1,
            "SCENARIO_COVERAGE_RECEIPT": True,
            "CHANGE_ID": CHANGE_ID,
            "MODE": "plan",
            "PLAN_STATUS": "ready",
            "PLAN_CONTENT_HASH": _lf_hash(plan),
            "MATERIALIZATION_STATUS": "COMMITTED",
            "INPUTS": {
                "DESIGN_REVISION": {
                    "path": "design.md",
                    "content_hash": _lf_hash(change / "design.md"),
                },
                "SPEC_REVISION": [
                    {
                        "path": "specs/risk/spec.md",
                        "content_hash": _lf_hash(
                            change / "specs" / "risk" / "spec.md"
                        ),
                    }
                ],
                "OWNER_CONTRACT_REVISION": {
                    "path": "owner-contract.md",
                    "content_hash": _lf_hash(change / "owner-contract.md"),
                },
            },
        },
    )
    return plan, receipt


def test_planning_requires_current_same_turn_ocr_pass(tmp_path: Path) -> None:
    """Missing current OCR evidence fails without creating a receipt."""

    change = _change(tmp_path)
    payload = change / "classification.json"
    _write_json(payload, _planning_classification())
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "planning",
            "--change-id",
            CHANGE_ID,
            "--change-root",
            str(change),
            "--classification",
            str(payload),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode != 0
    assert "OWNER_CONTRACT_REVIEW_NOT_PASS" in result.stderr
    assert not (change / "opsx").exists()


def test_planning_writes_hash_bound_receipt_only(tmp_path: Path) -> None:
    """Planning mode writes only planning-risk with current contract identity."""

    change = _change(tmp_path)
    result = _run(change, "planning", _planning_classification("HIGH"))
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    receipt = change / "opsx" / "planning-risk.md"
    assert output["RISK_PROFILE"] == "HIGH"
    body = receipt.read_text(encoding="utf-8")
    contract_hash = hashlib.sha256(
        (change / "owner-contract.md").read_bytes()
    ).hexdigest()
    assert f"SOURCE_OWNER_CONTRACT_SHA256: {contract_hash}" in body
    assert "CONSUMER_INTEGRATION: PARTIAL" in body
    assert not (change / "opsx" / "tasking-risk.md").exists()


def test_tasking_profile_is_independent_and_binds_solution_set(
    tmp_path: Path,
) -> None:
    """Tasking may be LOW after planning HIGH and hashes every chosen input."""

    change = _change(tmp_path)
    planning = _run(change, "planning", _planning_classification("HIGH"))
    assert planning.returncode == 0, planning.stderr
    _prepare_solution(change)
    result = _run(change, "tasking", _tasking_classification("LOW"))
    assert result.returncode == 0, result.stderr
    body = (change / "opsx" / "tasking-risk.md").read_text(encoding="utf-8")
    assert "RISK_PROFILE: LOW" in body
    assert "path: proposal.md" in body
    assert "path: design.md" in body
    assert "path: specs/risk/spec.md" in body
    assert "path: decisions/adapter/decision.md" in body
    assert body.count("sha256:") == 4
    assert "RISK_PROFILE: HIGH" not in body


def test_tasking_rejects_stale_planning_provenance_without_overwrite(
    tmp_path: Path,
) -> None:
    """Changed Owner Contract invalidates planning-risk and preserves tasking."""

    change = _change(tmp_path)
    planning = _run(change, "planning", _planning_classification())
    assert planning.returncode == 0, planning.stderr
    _prepare_solution(change)
    first = _run(change, "tasking", _tasking_classification())
    assert first.returncode == 0, first.stderr
    receipt = change / "opsx" / "tasking-risk.md"
    before = receipt.read_bytes()
    _ = (change / "owner-contract.md").write_text(
        "# Owner Contract\nChanged ownership bytes.\n"
    )
    stale = _run(change, "tasking", _tasking_classification("HIGH"))
    assert stale.returncode != 0
    assert "PLANNING_RISK_PROVENANCE_STALE" in stale.stderr
    assert receipt.read_bytes() == before


def test_tasking_fails_closed_on_missing_chosen_solution(tmp_path: Path) -> None:
    """Tasking cannot classify by absence when design/specs are missing."""

    change = _change(tmp_path)
    planning = _run(change, "planning", _planning_classification())
    assert planning.returncode == 0, planning.stderr
    result = _run(change, "tasking", _tasking_classification())
    assert result.returncode != 0
    assert "CHOSEN_SOLUTION_MISSING" in result.stderr
    assert not (change / "opsx" / "tasking-risk.md").exists()


def test_required_scenario_pair_must_be_current_and_is_bound(
    tmp_path: Path,
) -> None:
    """Required scenario evidence is validated and included in source identity."""

    change = _change(tmp_path)
    assert _run(change, "planning", _planning_classification()).returncode == 0
    _prepare_solution(change)
    plan, receipt = _prepare_scenario_pair(change)
    current = _run(
        change,
        "tasking",
        _tasking_classification(),
        scenario_evidence="required",
    )
    assert current.returncode == 0, current.stderr
    body = (change / "opsx" / "tasking-risk.md").read_text(encoding="utf-8")
    assert "path: scenario-coverage/plan.md" in body
    assert "path: scenario-coverage/plan.receipt.json" in body

    _ = plan.write_text("# Scenario plan\nPLAN_STATUS: changed\n")
    stale = _run(
        change,
        "tasking",
        _tasking_classification(),
        scenario_evidence="required",
    )
    assert stale.returncode != 0
    assert "SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL" in stale.stderr
    assert receipt.is_file()


def test_tasking_firewall_rejects_verification_execution_wording(
    tmp_path: Path,
) -> None:
    """Tasking fields cannot prescribe test/check execution."""

    change = _change(tmp_path)
    assert _run(change, "planning", _planning_classification()).returncode == 0
    _prepare_solution(change)
    classification = _tasking_classification()
    classification["sequencing_constraints"] = [
        "Run /opsx-test before implementation is done."
    ]
    result = _run(change, "tasking", classification)
    assert result.returncode != 0
    assert "TASKING_OUTPUT_FIREWALL" in result.stderr
    assert not (change / "opsx" / "tasking-risk.md").exists()


def test_fired_planning_escalation_blocks_tasking(tmp_path: Path) -> None:
    """A fired planning boundary routes back instead of minting tasking risk."""

    change = _change(tmp_path)
    assert _run(change, "planning", _planning_classification()).returncode == 0
    _prepare_solution(change)
    result = _run(
        change,
        "tasking",
        _tasking_classification(),
        escalation="fired",
    )
    assert result.returncode != 0
    assert "PLANNING_RISK_ESCALATION_FIRED" in result.stderr
    assert not (change / "opsx" / "tasking-risk.md").exists()
