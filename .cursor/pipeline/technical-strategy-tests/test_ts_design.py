"""Focused stale, invalid, and idempotent tests for /opsx-ts-design.

The design action publishes only ts/TS-DESIGN.*. A review call without
independent evidence writes nothing. design.md and owner-contract bytes
stay untouched.
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
CHANGE_ID = "bounded-ts-design"


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


def _payload(**overrides: object) -> dict[str, Any]:
    """Return a READY payload that does not open a new architecture."""

    payload: dict[str, Any] = {
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
        "assessments": {
            name: {"status": "NOT_REQUIRED"}
            for name in (
                "migration_assessment",
                "rollout_assessment",
                "rollback_assessment",
            )
        },
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
    payload["assessments"]["backward_compatibility_assessment"] = {
        "status": "NO_IMPACT"
    }
    payload.update(overrides)
    return payload


def _run(
    change: Path,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    requested_status: str | None = None,
    block_reason: str | None = None,
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
        command.extend(["--payload", str(path)])
    if requested_status is not None:
        command.extend(["--requested-status", requested_status])
    if block_reason is not None:
        command.extend(["--block-reason", block_reason])
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _design_bytes(change: Path) -> tuple[bytes, bytes]:
    """Return the published TS-DESIGN pair."""

    directory = change / "ts"
    return (
        (directory / "TS-DESIGN.json").read_bytes(),
        (directory / "TS-DESIGN.md").read_bytes(),
    )


def test_ready_publish_is_idempotent_and_current(tmp_path: Path) -> None:
    """Two identical publishes keep the same bytes and a current bind."""

    change = _change(tmp_path)
    first = _run(
        change, "design", _payload(), requested_status="READY_FOR_REVIEW"
    )
    assert first.returncode == 0, first.stderr
    published = json.loads(first.stdout)
    assert published["STATUS"] == "TS_DESIGN_PUBLISHED"
    assert published["TS_STATUS"] == "READY_FOR_REVIEW"
    before = _design_bytes(change)
    second = _run(
        change, "design", _payload(), requested_status="READY_FOR_REVIEW"
    )
    assert second.returncode == 0, second.stderr
    assert _design_bytes(change) == before
    again = json.loads(second.stdout)
    assert again["TS_DESIGN_SHA256"] == published["TS_DESIGN_SHA256"]
    freshness = _run(change, "freshness")
    assert freshness.returncode == 0, freshness.stderr
    current = json.loads(freshness.stdout)
    assert current["FRESHNESS"] == "CURRENT"
    assert current["TS_DESIGN_SHA256"] == published["TS_DESIGN_SHA256"]
    assert current["FILES_CHANGED"] == "none"
    document = json.loads((change / "ts" / "TS-DESIGN.json").read_text(encoding="utf-8"))
    assert "planning_risk_sha256" not in document["upstream_provenance"]
    assert not (change / "ts" / "TS-REVIEW.json").exists()
    assert not (change / "design.md").exists()


def test_stale_claim_and_upstream_edit_do_not_silently_match(
    tmp_path: Path,
) -> None:
    """A contradictory bind writes nothing, and an edited input is STALE."""

    change = _change(tmp_path)
    seeded_review = change / "ts" / "TS-REVIEW.json"
    seeded_design = change / "design.md"
    seeded_review.parent.mkdir(parents=True)
    review_bytes = b'{"review_state":"PASS"}\n'
    design_bytes = b"# Design\nExisting design authority.\n"
    _ = seeded_review.write_bytes(review_bytes)
    _ = seeded_design.write_bytes(design_bytes)
    ready = _run(
        change, "design", _payload(), requested_status="READY_FOR_REVIEW"
    )
    assert ready.returncode == 0, ready.stderr
    before = _design_bytes(change)
    stale = _run(
        change,
        "design",
        _payload(
            upstream_provenance={"input_revision_sha256": "0" * 64}
        ),
        requested_status="READY_FOR_REVIEW",
    )
    assert stale.returncode != 0
    assert "INPUT_REVISION_STALE" in stale.stderr
    assert _design_bytes(change) == before
    assert seeded_review.read_bytes() == review_bytes
    assert seeded_design.read_bytes() == design_bytes
    owner = change / "owner-contract.md"
    _ = owner.write_text(owner.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    freshness = _run(change, "freshness")
    assert freshness.returncode == 0, freshness.stderr
    assert json.loads(freshness.stdout)["FRESHNESS"] == "STALE"
    assert _design_bytes(change) == before
    reconciled = _run(
        change, "design", _payload(), requested_status="READY_FOR_REVIEW"
    )
    assert reconciled.returncode == 0, reconciled.stderr
    assert _design_bytes(change) != before
    assert json.loads(_run(change, "freshness").stdout)["FRESHNESS"] == "CURRENT"
    assert seeded_review.read_bytes() == review_bytes
    assert seeded_design.read_bytes() == design_bytes


def test_invalid_input_writes_nothing(tmp_path: Path) -> None:
    """Placeholders, missing specs, and review mode create no strategy pair."""

    change = _change(tmp_path)
    invalid = _run(
        change,
        "design",
        _payload(problem_model="UNKNOWN"),
        requested_status="READY_FOR_REVIEW",
    )
    assert invalid.returncode != 0
    assert "PLACEHOLDER_FORBIDDEN" in invalid.stderr
    assert not (change / "ts").exists()
    missing = _change(tmp_path / "missing")
    _ = (missing / "specs" / "strategy" / "spec.md").unlink()
    blocked = _run(
        missing, "design", _payload(), requested_status="READY_FOR_REVIEW"
    )
    assert blocked.returncode != 0
    assert "UPSTREAM_SPECS_MISSING" in blocked.stderr
    assert not (missing / "ts").exists()
    alternatives = _run(
        change,
        "design",
        _payload(
            strategy_class="NEW_ARCHITECTURE",
            new_architectural_boundary=True,
        ),
        requested_status="READY_FOR_REVIEW",
    )
    assert alternatives.returncode != 0
    assert "ALTERNATIVES_REQUIRED" in alternatives.stderr
    assert not (change / "ts").exists()
    review = _run(change, "review")
    assert review.returncode != 0
    assert "INDEPENDENT_REVIEW_REQUIRED" in review.stderr
    assert not (change / "ts" / "TS-REVIEW.md").exists()
