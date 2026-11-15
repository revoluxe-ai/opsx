"""Shared read-only planning and verification readiness contract.

Planning checks plan+receipt against source inputs, while verify additionally
requires the authored trace matrix. No writer capabilities live here.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

# CheckWorkflowReceipt type for optional injected receipt on preflight agent

from agents.common.scenario_files import (
    expected_plan_content_hash,
    plan_has_embedded_rows,
    read_scenario_ids,
)
from check_workflow_receipt import CheckWorkflowReceipt
from agents.common.scenario_migrate import check_coverage, layout_problems_block_verify_preflight
from agents.state import GraphState

from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot
from workflows.artifact_paths import scenario_machine_receipt_path

_CHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _result(status: str, code: str, detail: str, *, producer: str = "") -> dict[str, Any]:
    return {"status": status, "code": code, "detail": detail, "producer": producer}


def inspect_plan_artifacts(
    repo_root: Path, change_id: str, *, phase: str = "verify",
) -> dict[str, Any]:
    """Validate the applicable plan inputs, not an arbitrary fixed file inventory."""
    root = repo_root.resolve()
    if phase not in ("planning", "verify"):
        return _result("BLOCKED", "PLAN_PHASE_INVALID", phase)
    if not _CHANGE.fullmatch(change_id):
        return _result("BLOCKED", "CHANGE_ID_INVALID", change_id)
    change = resource_path(root, "openspec", "changes", change_id)
    if not change.is_dir() or change.is_symlink():
        return _result("BLOCKED", "CHANGE_MISSING", str(change))
    specs = sorted((change / "specs").rglob("*.md")) if (change / "specs").is_dir() else []
    planning = ("proposal.md", "design.md", "tasks.md")
    # A real planned change must have the core trio. A test-only/synthetic
    # change without specs or planning sources is not silently given a PASS.
    if not specs and not any((change / n).exists() for n in planning):
        return _result("BLOCKED", "PLANNING_INPUTS_MISSING", str(change),
                       producer="/opsx-continue|/opsx-update")
    if specs or any((change / n).exists() for n in planning):
        for name in planning:
            if not (change / name).is_file():
                return _result("BLOCKED", "PLAN_INPUT_MISSING", name,
                               producer="/opsx-continue|/opsx-update")
        if not specs:
            return _result("BLOCKED", "SPEC_INPUT_MISSING", "specs/*.md",
                           producer="/opsx-continue|/opsx-update")
    for p in specs:
        if p.is_symlink() or not p.is_file():
            return _result("BLOCKED", "SPEC_INPUT_INVALID", str(p))
    directory = change / "scenario-coverage"
    plan = directory / "plan.md"
    receipt_path = scenario_machine_receipt_path(root, change_id, "plan.receipt.json")
    for path in (plan, receipt_path):
        if not path.is_file() or path.is_symlink():
            return _result("BLOCKED", "COVERAGE_PLAN_MISSING", str(path),
                           producer="/opsx-spec-scenario-coverage mode=plan")
    try:
        raw = plan.read_bytes()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _result("BLOCKED", "COVERAGE_PLAN_INVALID", str(exc),
                       producer="/opsx-spec-scenario-coverage mode=plan")
    if not isinstance(receipt, dict) or receipt.get("SCENARIO_COVERAGE_RECEIPT") is not True:
        return _result("BLOCKED", "COVERAGE_RECEIPT_INVALID", str(receipt_path),
                       producer="/opsx-spec-scenario-coverage mode=plan")
    if receipt.get("PLAN_STATUS") not in ("ready", "provisional"):
        return _result("BLOCKED", "COVERAGE_PLAN_NOT_READY", str(receipt.get("PLAN_STATUS")),
                       producer="/opsx-spec-scenario-coverage mode=plan")
    if receipt.get("CHANGE_ID") not in (None, change_id):
        return _result("BLOCKED", "COVERAGE_CHANGE_MISMATCH", str(receipt.get("CHANGE_ID")))
    text = raw.decode("utf-8", errors="replace")
    if plan_has_embedded_rows(text):
        return _result(
            "BLOCKED", "COVERAGE_PLAN_EMBEDDED",
            "Scenario bodies remain in plan.md",
            producer="/opsx-spec-scenario-coverage mode=plan",
        )
    try:
        rows = read_scenario_ids(directory, allow_historical=False)
    except ValueError as exc:
        return _result("BLOCKED", "COVERAGE_ROWS_INVALID", str(exc),
                       producer="/opsx-spec-scenario-coverage mode=plan")
    if len(rows) != len(set(rows)) or (specs and not rows):
        return _result("BLOCKED", "COVERAGE_ROWS_INVALID", "Missing/duplicate SCENARIO_ID")
    if rows:
        content_hash = receipt.get("PLAN_CONTENT_HASH")
        if not content_hash or content_hash != expected_plan_content_hash(directory):
            return _result("BLOCKED", "COVERAGE_PLAN_HASH_MISMATCH", str(plan),
                           producer="/opsx-spec-scenario-coverage mode=plan")
        if not receipt.get("PLAN_EVIDENCE_ID"):
            return _result("BLOCKED", "COVERAGE_ID_MISSING", str(receipt_path),
                           producer="/opsx-spec-scenario-coverage mode=plan")
    # Verify every declared planning input on disk; input names are scoped
    # to this change and cannot escape it.
    sources: list[Mapping[str, Any]] = []
    inputs = receipt.get("INPUTS")
    if rows and not isinstance(inputs, dict):
        return _result("BLOCKED", "COVERAGE_INPUTS_MISSING", str(receipt_path),
                       producer="/opsx-spec-scenario-coverage mode=plan")
    if isinstance(inputs, dict):
        for value in inputs.values():
            if isinstance(value, list):
                sources.extend(x for x in value if isinstance(x, Mapping))
            elif isinstance(value, Mapping) and (value.get("path") or value.get("source")):
                sources.append(value)
        for entry in sources:
            rel = entry.get("path") or entry.get("source")
            digest = entry.get("content_hash")
            if not isinstance(rel, str) or not isinstance(digest, str):
                return _result("BLOCKED", "COVERAGE_INPUT_INVALID", str(entry))
            file_path = (change / rel).resolve()
            if not file_path.is_relative_to(change.resolve()) or not file_path.is_file():
                return _result("BLOCKED", "COVERAGE_INPUT_MISSING", rel,
                               producer="/opsx-continue|/opsx-update")
            if not CheckWorkflowReceipt.matches_file(file_path, digest):
                return _result("BLOCKED", "COVERAGE_INPUT_STALE", rel,
                               producer="/opsx-spec-scenario-coverage mode=plan")

    matrix_path = scenario_machine_receipt_path(root, change_id, "test_trace_matrix.json")
    if phase == "verify" and rows:
        if not matrix_path.is_file() or matrix_path.is_symlink():
            return _result("BLOCKED", "TRACE_MATRIX_MISSING", str(matrix_path),
                           producer="/opsx-test → scenario trace materialization")
        try:
            matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return _result("BLOCKED", "TRACE_MATRIX_INVALID", str(exc),
                           producer="/opsx-test → scenario trace materialization")
        if not isinstance(matrix, dict) or not isinstance(matrix.get("entries"), list):
            return _result("BLOCKED", "TRACE_MATRIX_INVALID", "entries must be an array")
        if matrix.get("change_id") not in (None, change_id):
            return _result("BLOCKED", "TRACE_MATRIX_CHANGE_MISMATCH", str(matrix.get("change_id")))
        if matrix.get("schema_version") not in (None, 1):
            return _result("BLOCKED", "TRACE_MATRIX_SCHEMA_UNSUPPORTED", str(matrix.get("schema_version")))
        if matrix.get("plan_evidence_id") != receipt.get("PLAN_EVIDENCE_ID"):
            return _result("BLOCKED", "TRACE_MATRIX_STALE", str(matrix_path),
                           producer="/opsx-test → scenario trace materialization")
        found = matrix["entries"]
        actual = [x.get("scenario_id") if isinstance(x, dict) else None for x in found]
        if set(actual) != set(rows) or len(actual) != len(rows):
            return _result("BLOCKED", "TRACE_MATRIX_ROW_MISMATCH", str(matrix_path),
                           producer="scenario_trace_materialize")
        if any(not isinstance(x.get("test_node_id"), str) for x in found):
            return _result("BLOCKED", "TRACE_MATRIX_INVALID", "test_node_id must be a string")
        tests_root = (resource_path(root, "tests")).resolve()
        for entry in found:
            node_id = entry["test_node_id"]
            if not node_id.strip():
                continue
            node_path = node_id.partition("::")[0]
            path = (resource_path(root, node_path)).resolve()
            if not node_path.startswith("tests/") or not path.is_relative_to(tests_root):
                return _result(
                    "BLOCKED",
                    "TRACE_TEST_MISSING",
                    entry["test_node_id"],
                    producer="/opsx-test",
                )
            if path.is_symlink():
                return _result(
                    "BLOCKED",
                    "TRACE_TEST_MISSING",
                    entry["test_node_id"],
                    producer="/opsx-test",
                )
            # Отсутствующий mapped test чинится построчным repair, не блокируем verify.
            if not path.is_file():
                continue
        layout = check_coverage(directory, repo=root, require_canonical_tests=True)
        hard_layout = layout_problems_block_verify_preflight(layout)
        if hard_layout:
            return _result(
                "BLOCKED",
                hard_layout[0],
                ", ".join(hard_layout),
                producer="run_scenario_plan_migrate --check",
            )
        # Audit validates the behavioral mapping, its source lines and proofs.
        # This gate only validates producer/consumer preconditions.
    try:
        snapshot = input_snapshot(root, change_id) if phase == "verify" and rows else None
    except (ValueError, OSError) as exc:
        return _result("BLOCKED", "SCENARIO_SOURCE_INVALID", str(exc))
    return {
        **_result("READY", "PLANNING_INPUTS_READY" if phase == "planning" else "PLAN_ARTIFACTS_READY",
                  "Planning inputs verified; no scenario coverage verdict issued"),
        "phase": phase,
        "source_digest": (
            snapshot["source_digest"] if snapshot else
            hashlib.sha256(raw + receipt_path.read_bytes()).hexdigest()
        ),
        "plan_evidence_id": receipt.get("PLAN_EVIDENCE_ID", ""),
        "scenario_count": len(rows),
    }




class PlanningArtifactsPreflightAgent:
    """Deterministic planning-stage gate: accepts only plan+receipt/source identity."""

    def __init__(self, receipt: Any | None = None) -> None:
        self._receipt = receipt if receipt is not None else CheckWorkflowReceipt.none()

    async def run(self, state: GraphState) -> dict[str, Any]:
        result = inspect_plan_artifacts(
            Path(str(state["repository_path"])),
            str(state.get("change_id") or ""),
            phase="planning",
        )
        return {
            "planning_readiness_status": result["status"],
            "planning_readiness_result": result,
        }
