"""Independent TS-REVIEW tests for stale, forbidden, and reused binds.

The review action writes only ts/TS-REVIEW.* after an explicit reviewer id
and evidence file. It does not mint PASS from the design writer and does
not modify TS-DESIGN or design.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


HELPER = (
    Path(__file__).resolve().parents[1]
    / "technical-strategy"
    / "technical_strategy.py"
)
CHANGE_ID = "bounded-ts-review"
REVIEWER_ID = "lane-reviewer"


def _write_json(path: Path, value: object) -> None:
    """Write a deterministic JSON fixture."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _change(tmp_path: Path) -> Path:
    """Create the minimum current upstream inputs for one Change."""

    change = tmp_path / "openspec" / "changes" / CHANGE_ID
    change.mkdir(parents=True)
    _ = (change / "owner-contract.md").write_text(
        "# Owner Contract\nOne write owner remains explicit.\n",
        encoding="utf-8",
    )
    _ = (change / "proposal.md").write_text(
        "# Proposal\nKeep the existing write owner.\n",
        encoding="utf-8",
    )
    spec = change / "specs" / "strategy" / "spec.md"
    spec.parent.mkdir(parents=True)
    _ = spec.write_text(
        "# Spec\n## Requirement\nThe strategy names the current write owner.\n",
        encoding="utf-8",
    )
    return change


def _payload() -> dict[str, Any]:
    """Return a READY payload that does not open a new architecture."""

    assessments: dict[str, Any] = {
        name: {"status": "NOT_REQUIRED"}
        for name in (
            "migration_assessment",
            "rollout_assessment",
            "rollback_assessment",
        )
    }
    assessments["backward_compatibility_assessment"] = {"status": "NO_IMPACT"}
    return {
        "problem_model": "The change must name the existing write owner.",
        "owner_outcome_to_preserve": "Owner semantics stay in owner-contract.",
        "constraints": ["Do not rewrite design.md from this command."],
        "assumptions": ["Proposal and specs are already current."],
        "existing_architecture_context": "The current module owns the write.",
        "strategy_class": "NO_MATERIAL_ARCHITECTURAL_CHANGE",
        "new_architectural_boundary": False,
        "candidates": [
            {
                "id": "keep-current-owner",
                "summary": "Keep the existing write owner.",
            }
        ],
        "recommended_strategy_id": "keep-current-owner",
        "rejected_strategies_with_reason": [],
        "core_decisions": {
            key: {"status": "SPECIFIED", "detail": f"{key} stays local."}
            for key in (
                "sot_placement",
                "durability",
                "write_authority",
                "recovery",
            )
        },
        "assessments": assessments,
        "state_ownership": "The current module owns durable state.",
        "data_flow": "Inputs stay inside the change root.",
        "control_flow": "Publish only after live hashes match.",
        "concurrency_model": "One publisher replaces the pair atomically.",
        "failure_model": "Invalid input leaves the pair unchanged.",
        "consistency_model": "Markdown hash matches the canonical JSON hash.",
        "persistence_model": "Only TS-DESIGN files are durable outputs.",
        "security_boundaries": "No product credentials are read.",
        "observability_requirements": "The helper prints one JSON result.",
        "testability_implications": "Stale and invalid paths write nothing.",
        "open_technical_risks": ["Downstream design writers do not consume this."],
    }


def _run(
    change: Path,
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    requested_status: str | None = None,
    findings: dict[str, Any] | None = None,
    reviewer_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke one helper action against a fixture Change."""

    command = [
        sys.executable,
        str(HELPER),
        action,
        "--change-id",
        CHANGE_ID,
        "--change-root",
        str(change),
    ]
    if payload is not None:
        path = change.parent / "ts-design-payload.json"
        _write_json(path, payload)
        command.extend(["--payload", str(path), "--requested-status", requested_status or ""])
    if findings is not None:
        path = change.parent / "ts-review-evidence.json"
        _write_json(path, findings)
        command.extend(["--findings", str(path)])
    if reviewer_id is not None:
        command.extend(["--reviewer-id", reviewer_id])
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _publish_ready(change: Path) -> dict[str, Any]:
    """Publish one READY design and return the helper result."""

    result = _run(
        change,
        "design",
        payload=_payload(),
        requested_status="READY_FOR_REVIEW",
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _evidence(
    published: dict[str, Any],
    findings: list[dict[str, str]] | None = None,
    **overrides: object,
) -> dict[str, Any]:
    """Build independent evidence bound to one published design."""

    evidence: dict[str, Any] = {
        "reviewer_id": REVIEWER_ID,
        "independence": "INDEPENDENT_OF_TS_DESIGN_WRITER",
        "bound_ts_design_sha256": published["TS_DESIGN_SHA256"],
        "bound_input_revision_sha256": published["INPUT_REVISION_SHA256"],
        "findings": findings or [],
    }
    evidence.update(overrides)
    return evidence


def _finding(**overrides: str) -> dict[str, str]:
    """Return one complete finding, with optional field overrides."""

    finding = {
        "id": "F-1",
        "type": "ARCHITECTURE_DEFECT",
        "severity": "BLOCKING",
        "violated_authority": "ts/TS-DESIGN.json",
        "concrete_problem": "The strategy names the wrong write owner.",
        "consequence": "design.md would hide the strategy defect.",
        "evidence": "recommended_strategy_id does not match the spec.",
        "required_correction_class": "CORRECT_TS_DESIGN",
        "target_artifact": "ts/TS-DESIGN.json",
    }
    finding.update(overrides)
    return finding


def _review_bytes(change: Path) -> tuple[bytes, bytes]:
    """Return the published TS-REVIEW pair."""

    directory = change / "ts"
    return (
        (directory / "TS-REVIEW.json").read_bytes(),
        (directory / "TS-REVIEW.md").read_bytes(),
    )


def _design_bytes(change: Path) -> bytes:
    """Return the published TS-DESIGN JSON bytes."""

    return (change / "ts" / "TS-DESIGN.json").read_bytes()


def test_stale_design_blocks_without_mutating_design(tmp_path: Path) -> None:
    """An edited owner-contract makes review BLOCKED and leaves design bytes."""

    change = _change(tmp_path)
    published = _publish_ready(change)
    before = _design_bytes(change)
    owner = change / "owner-contract.md"
    _ = owner.write_text(
        owner.read_text(encoding="utf-8") + "\nEdited.\n",
        encoding="utf-8",
    )
    review = _run(
        change,
        "review",
        findings=_evidence(published),
        reviewer_id=REVIEWER_ID,
    )
    assert review.returncode == 0, review.stderr
    body = json.loads(review.stdout)
    assert body["REVIEW_STATE"] == "BLOCKED"
    assert body["BLOCK_REASON"] == "INPUT_REVISION_STALE"
    assert body["NEXT_ACTION"] == "RECONCILE_TS_DESIGN"
    assert body["DESIGN_UNLOCK"] == "NOT_WIRED"
    assert _design_bytes(change) == before
    assert not (change / "design.md").exists()
    status = json.loads(_run(change, "status").stdout)
    assert status["CURRENT_PASS"] is False


def test_malformed_or_forbidden_finding_writes_nothing(tmp_path: Path) -> None:
    """Forbidden finding types and missing fields leave any prior review intact."""

    change = _change(tmp_path)
    published = _publish_ready(change)
    seeded = change / "ts" / "TS-REVIEW.json"
    seeded_bytes = b'{"review_state":"KEEP"}\n'
    _ = seeded.write_bytes(seeded_bytes)
    design_before = _design_bytes(change)
    forbidden = _run(
        change,
        "review",
        findings=_evidence(published, [_finding(type="OWNER_DECISION")]),
        reviewer_id=REVIEWER_ID,
    )
    assert forbidden.returncode != 0
    assert "OWNER_DECISION_FORBIDDEN" in forbidden.stderr
    assert seeded.read_bytes() == seeded_bytes
    hidden = _run(
        change,
        "review",
        findings=_evidence(
            published,
            [_finding(type="SPEC_DRIFT", target_artifact="design.md")],
        ),
        reviewer_id=REVIEWER_ID,
    )
    assert hidden.returncode != 0
    assert "DESIGN_MD_TARGET_FORBIDDEN" in hidden.stderr
    incomplete = _run(
        change,
        "review",
        findings=_evidence(published, [_finding(severity="")]),
        reviewer_id=REVIEWER_ID,
    )
    assert incomplete.returncode != 0
    assert "FINDING_FIELD_REQUIRED:severity" in incomplete.stderr
    assert seeded.read_bytes() == seeded_bytes
    assert _design_bytes(change) == design_before
    assert not (change / "ts" / "TS-REVIEW.md").exists()


def test_untrusted_pass_and_design_writer_are_rejected(tmp_path: Path) -> None:
    """A claimed PASS and a design-writer id do not become a current receipt."""

    change = _change(tmp_path)
    published = _publish_ready(change)
    claimed = _run(
        change,
        "review",
        findings=_evidence(published, review_state="PASS"),
        reviewer_id=REVIEWER_ID,
    )
    assert claimed.returncode != 0
    assert "UNTRUSTED_PASS_CLAIM" in claimed.stderr
    assert not (change / "ts" / "TS-REVIEW.json").exists()
    self_review = _run(
        change,
        "review",
        findings=_evidence(published, reviewer_id="opsx-ts-design"),
        reviewer_id="opsx-ts-design",
    )
    assert self_review.returncode != 0
    assert "REVIEWER_NOT_INDEPENDENT" in self_review.stderr
    forged = {
        "review_state": "PASS",
        "findings": [],
        "blocking_finding_count": 0,
        "bound_ts_design_sha256": "0" * 64,
        "bound_input_revision_sha256": "0" * 64,
        "verified_state": "NOT_GRANTED",
        "design_unlock": "NOT_WIRED",
    }
    _write_json(change / "ts" / "TS-REVIEW.json", forged)
    status = json.loads(_run(change, "status").stdout)
    assert status["CURRENT_PASS"] is False
    assert status["REASON"] == "NOT_CURRENT_PASS"
    assert _design_bytes(change) == (change / "ts" / "TS-DESIGN.json").read_bytes()


def test_pass_receipt_is_idempotent(tmp_path: Path) -> None:
    """Two identical independent reviews keep the same PASS bytes."""

    change = _change(tmp_path)
    published = _publish_ready(change)
    evidence = _evidence(published)
    first = _run(change, "review", findings=evidence, reviewer_id=REVIEWER_ID)
    assert first.returncode == 0, first.stderr
    body = json.loads(first.stdout)
    assert body["REVIEW_STATE"] == "PASS"
    assert body["BLOCKING_FINDING_COUNT"] == 0
    assert body["NEXT_ACTION"] == "STOP_DESIGN_UNLOCK_NOT_WIRED"
    assert body["CONSUMER_INTEGRATION"] == "MISSING"
    before = _review_bytes(change)
    design_before = _design_bytes(change)
    second = _run(change, "review", findings=evidence, reviewer_id=REVIEWER_ID)
    assert second.returncode == 0, second.stderr
    assert _review_bytes(change) == before
    assert json.loads(second.stdout)["BOUND_TS_DESIGN_SHA256"] == body[
        "BOUND_TS_DESIGN_SHA256"
    ]
    assert _design_bytes(change) == design_before
    status = json.loads(_run(change, "status").stdout)
    assert status["CURRENT_PASS"] is True
    document = json.loads((change / "ts" / "TS-REVIEW.json").read_text(encoding="utf-8"))
    assert document["source_input_identities"]["owner_contract_sha256"]
    assert document["design_unlock"] == "NOT_WIRED"


def test_cross_revision_evidence_cannot_reuse_pass(tmp_path: Path) -> None:
    """Evidence bound to the previous design hash cannot remain a current PASS."""

    change = _change(tmp_path)
    first_design = _publish_ready(change)
    passed = _run(
        change,
        "review",
        findings=_evidence(first_design),
        reviewer_id=REVIEWER_ID,
    )
    assert passed.returncode == 0, passed.stderr
    old_review = _review_bytes(change)
    owner = change / "owner-contract.md"
    _ = owner.write_text(
        owner.read_text(encoding="utf-8") + "\nRevised.\n",
        encoding="utf-8",
    )
    second_design = _publish_ready(change)
    assert second_design["TS_DESIGN_SHA256"] != first_design["TS_DESIGN_SHA256"]
    stale_status = json.loads(_run(change, "status").stdout)
    assert stale_status["CURRENT_PASS"] is False
    design_before = _design_bytes(change)
    reused = _run(
        change,
        "review",
        findings=_evidence(first_design),
        reviewer_id=REVIEWER_ID,
    )
    assert reused.returncode == 0, reused.stderr
    body = json.loads(reused.stdout)
    assert body["REVIEW_STATE"] == "BLOCKED"
    assert body["BLOCK_REASON"] == "CROSS_REVISION_REUSE"
    assert body["BOUND_TS_DESIGN_SHA256"] == second_design["TS_DESIGN_SHA256"]
    assert body["NEXT_ACTION"] == "RECONCILE_TS_DESIGN"
    assert _review_bytes(change) != old_review
    assert _design_bytes(change) == design_before
    current = json.loads(_run(change, "status").stdout)
    assert current["CURRENT_PASS"] is False
