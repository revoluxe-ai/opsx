"""In-process mock harness for VerificationExecutionWorkflow scenarios.

Does not touch live openspec ledgers. Simulates next-check + executor +
durable result files under a temp verify tree when needed.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verification_engine import (
    TERMINAL_SUCCESS,
    AssignedCheckResult,
    NextCheckResult,
    build_execute_prompt,
    build_next_check_prompt,
)


def _assignment(
    *,
    change: str,
    plan_id: str,
    check_id: str,
    epoch: int,
    executor: str = "opsx-run-tests",
    capability: str = "frontend_tests",
) -> dict[str, Any]:
    safe = check_id.replace("\u00d7", "--").replace("×", "--")
    return {
        "assignment_schema_version": 1,
        "change_id": change,
        "plan_id": plan_id,
        "required_check_id": check_id,
        "target_id": f"{change}.target",
        "capability_id": capability,
        "executor": executor,
        "executor_mode": "bounded",
        "binding_fingerprint": f"fp-{check_id}",
        "verification_frontier_hash": "frontier-mock",
        "implementation_snapshot": {
            "provenance_head": "deadbeef",
            "content_paths": ["src/App.vue"],
            "content_digest": "digest-mock",
        },
        "bindings_path": f"openspec/changes/{change}/verify/EXECUTOR-BINDINGS.json",
        "expected_result_path": (
            f"openspec/changes/{change}/verify/check-results/{plan_id}/{safe}.json"
        ),
        "assignment_epoch": epoch,
    }


def _result_payload(assignment: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "change_id": assignment["change_id"],
        "plan_id": assignment["plan_id"],
        "required_check_id": assignment["required_check_id"],
        "target_id": assignment["target_id"],
        "capability_id": assignment["capability_id"],
        "executor": assignment["executor"],
        "executor_mode": "bounded",
        "binding_fingerprint": assignment["binding_fingerprint"],
        "verification_frontier_hash": assignment["verification_frontier_hash"],
        "implementation_snapshot": assignment["implementation_snapshot"],
        "STATUS": status,
        "created_at": "2026-09-01T00:00:00Z",
    }


@dataclass
class VerificationMockHarness:
    scenario: str
    change: str
    repo: Path
    plan_id: str = "vp-mock-verification"
    check_ids: list[str] = field(default_factory=list)
    # outcomes per check index: PASS | FAIL | BLOCKED
    outcomes: list[str] = field(default_factory=list)
    pass_count: int = 0
    next_check_calls: int = 0
    executor_calls: int = 0
    current_index: int = 0
    current_last_result: str | None = None
    current_assignment: dict[str, Any] | None = None
    snapshot_drift: bool = False
    evidence_mode: str | None = None  # missing | invalid | None
    next_check_transient_left: int = 0
    executor_mode: str | None = None  # lost_with_result | uncertain_no_result | None
    _written_results: dict[str, str] = field(default_factory=dict)
    _force_evidence_flags: bool = False

    @classmethod
    def from_scenario(cls, scenario: str, *, change: str, repo: Path | str) -> "VerificationMockHarness":
        repo_path = Path(repo)
        name = scenario
        if name in ("verification-all-pass", "verification-scenario1"):
            ids = ["check-a", "check-b", "check-c"]
            return cls(name, change, repo_path, check_ids=ids, outcomes=["PASS", "PASS", "PASS"])
        if name in ("verification-second-fail", "verification-scenario2"):
            ids = ["check-a", "check-b", "check-c"]
            return cls(name, change, repo_path, check_ids=ids, outcomes=["PASS", "FAIL", "PASS"])
        if name in ("verification-executor-blocked", "verification-scenario3"):
            ids = ["check-a", "check-b"]
            return cls(name, change, repo_path, check_ids=ids, outcomes=["BLOCKED", "PASS"])
        if name in ("verification-snapshot-drift", "verification-scenario4"):
            return cls(name, change, repo_path, check_ids=["check-a"], outcomes=["PASS"], snapshot_drift=True)
        if name in ("verification-invalid-evidence", "verification-scenario5"):
            ids = ["check-a"]
            return cls(
                name,
                change,
                repo_path,
                check_ids=ids,
                outcomes=["PASS"],
                evidence_mode="invalid",
            )
        if name in ("verification-next-check-transient", "verification-scenario6"):
            ids = ["check-a"]
            return cls(
                name,
                change,
                repo_path,
                check_ids=ids,
                outcomes=["PASS"],
                next_check_transient_left=1,
            )
        if name in ("verification-executor-lost-but-result", "verification-scenario7"):
            ids = ["check-a"]
            return cls(
                name,
                change,
                repo_path,
                check_ids=ids,
                outcomes=["PASS"],
                executor_mode="lost_with_result",
            )
        if name in ("verification-executor-uncertain", "verification-scenario8"):
            ids = ["check-a"]
            return cls(
                name,
                change,
                repo_path,
                check_ids=ids,
                outcomes=["PASS"],
                executor_mode="uncertain_no_result",
            )
        if name in ("verification-ten-checks", "verification-scenario9"):
            ids = [f"check-{i:02d}" for i in range(1, 11)]
            return cls(name, change, repo_path, check_ids=ids, outcomes=["PASS"] * 10)
        raise ValueError(f"Unknown verification mock scenario: {scenario}")

    def _total(self) -> int:
        return len(self.check_ids)

    def _promote_current(self) -> None:
        if self.current_index >= self._total():
            self.current_assignment = None
            self.current_last_result = None
            return
        check_id = self.check_ids[self.current_index]
        self.current_assignment = _assignment(
            change=self.change,
            plan_id=self.plan_id,
            check_id=check_id,
            epoch=self.current_index + 1,
        )
        self.current_last_result = None

    def run_next_check(self) -> NextCheckResult:
        self.next_check_calls += 1
        prompt = build_next_check_prompt(self.change)

        if self.next_check_transient_left > 0:
            self.next_check_transient_left -= 1
            return NextCheckResult(
                completed=False,
                status="NEXT_CHECK_TECHNICAL_FAILURE",
                progress_pass=self.pass_count,
                progress_total=self._total(),
                current_required_check_id=None,
                assignment_epoch=None,
                execute_assignment=None,
                current_last_result=None,
                block_reason="transient_cli",
                plan_id=self.plan_id,
                ledger_path=None,
                raw_output=f"STATUS: NEXT_CHECK_TECHNICAL_FAILURE\nPROMPT={prompt}",
            )

        if self.snapshot_drift:
            return NextCheckResult(
                completed=True,
                status="BLOCKED_SNAPSHOT_DRIFT",
                progress_pass=self.pass_count,
                progress_total=self._total(),
                current_required_check_id=None,
                assignment_epoch=None,
                execute_assignment=None,
                current_last_result=None,
                block_reason="content_digest_mismatch",
                plan_id=self.plan_id,
                ledger_path=None,
                raw_output="STATUS: BLOCKED_SNAPSHOT_DRIFT\nREASON: content_digest_mismatch",
            )

        # Reconcile durable result for CURRENT if present.
        if self.current_assignment is not None:
            expected = self.current_assignment["expected_result_path"]
            written = self._written_results.get(expected)
            if self.evidence_mode == "invalid" and self.executor_calls > 0 and not written:
                return NextCheckResult(
                    completed=True,
                    status="CHECK_REQUIRED",
                    progress_pass=self.pass_count,
                    progress_total=self._total(),
                    current_required_check_id=self.current_assignment["required_check_id"],
                    assignment_epoch=self.current_assignment["assignment_epoch"],
                    execute_assignment=dict(self.current_assignment),
                    current_last_result=None,
                    block_reason=None,
                    plan_id=self.plan_id,
                    ledger_path=None,
                    raw_output="STATUS: CHECK_REQUIRED\nEVIDENCE_INVALID",
                    evidence_invalid=True,
                )
            if self.evidence_mode == "missing" and self.executor_calls > 0 and not written:
                return NextCheckResult(
                    completed=True,
                    status="CHECK_REQUIRED",
                    progress_pass=self.pass_count,
                    progress_total=self._total(),
                    current_required_check_id=self.current_assignment["required_check_id"],
                    assignment_epoch=self.current_assignment["assignment_epoch"],
                    execute_assignment=dict(self.current_assignment),
                    current_last_result=None,
                    block_reason=None,
                    plan_id=self.plan_id,
                    ledger_path=None,
                    raw_output="STATUS: CHECK_REQUIRED\nEVIDENCE_MISSING",
                    evidence_missing=True,
                )
            if written == "PASS":
                self.pass_count += 1
                self.current_index += 1
                self.current_assignment = None
                self.current_last_result = None
                self._promote_current()
            elif written in ("FAIL", "BLOCKED"):
                self.current_last_result = written

        if self.current_assignment is None and self.pass_count >= self._total() and self._total() > 0:
            return NextCheckResult(
                completed=True,
                status=TERMINAL_SUCCESS,
                progress_pass=self.pass_count,
                progress_total=self._total(),
                current_required_check_id=None,
                assignment_epoch=None,
                execute_assignment=None,
                current_last_result=None,
                block_reason=None,
                plan_id=self.plan_id,
                ledger_path=None,
                raw_output=f"STATUS: {TERMINAL_SUCCESS}\nPROGRESS: {self.pass_count}/{self._total()} PASS",
            )

        if self.current_assignment is None:
            self._promote_current()

        if self.current_assignment is None:
            return NextCheckResult(
                completed=True,
                status=TERMINAL_SUCCESS,
                progress_pass=self.pass_count,
                progress_total=self._total(),
                current_required_check_id=None,
                assignment_epoch=None,
                execute_assignment=None,
                current_last_result=None,
                block_reason=None,
                plan_id=self.plan_id,
                ledger_path=None,
                raw_output=f"STATUS: {TERMINAL_SUCCESS}",
            )

        return NextCheckResult(
            completed=True,
            status="CHECK_REQUIRED",
            progress_pass=self.pass_count,
            progress_total=self._total(),
            current_required_check_id=self.current_assignment["required_check_id"],
            assignment_epoch=self.current_assignment["assignment_epoch"],
            execute_assignment=dict(self.current_assignment),
            current_last_result=self.current_last_result,
            block_reason=None,
            plan_id=self.plan_id,
            ledger_path=None,
            raw_output=(
                f"STATUS: CHECK_REQUIRED\n"
                f"REASON: EVIDENCE_MISSING\n"
                f"PROGRESS: {self.pass_count}/{self._total()} PASS\n"
                f"CURRENT_CHECK: {self.current_assignment['required_check_id']}"
            ),
            evidence_missing=True,
        )

    def _write_result(self, assignment: dict[str, Any], status: str) -> Path:
        rel = assignment["expected_result_path"]
        path = resource_path(self.repo, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _result_payload(assignment, status)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._written_results[rel] = status
        return path

    def run_assigned_check(self, assignment: dict[str, Any]) -> AssignedCheckResult:
        self.executor_calls += 1
        prompt = build_execute_prompt(assignment)
        assert prompt.startswith("/"), prompt

        if self.evidence_mode == "invalid":
            # Pretend executor finished but wrote garbage / no valid schema file.
            bad = resource_path(self.repo, assignment["expected_result_path"])
            bad.parent.mkdir(parents=True, exist_ok=True)
            bad.write_text("{not-json", encoding="utf-8")
            return AssignedCheckResult(
                completed=True,
                uncertain=False,
                durable_result_found=False,
                durable_result_status=None,
                expected_result_path=assignment["expected_result_path"],
                executor=assignment.get("executor"),
                required_check_id=assignment.get("required_check_id"),
                raw_output="executor claimed done; invalid result file",
                started=True,
                exit_code=0,
                fresh_session_id=f"mock-exec-{self.executor_calls}",
            )

        if self.executor_mode == "uncertain_no_result":
            return AssignedCheckResult(
                completed=False,
                uncertain=True,
                durable_result_found=False,
                durable_result_status=None,
                expected_result_path=assignment["expected_result_path"],
                executor=assignment.get("executor"),
                required_check_id=assignment.get("required_check_id"),
                raw_output="agent connection lost mid-run",
                started=True,
                exit_code=None,
                fresh_session_id=f"mock-exec-{self.executor_calls}",
            )

        outcome = self.outcomes[self.current_index]
        if self.executor_mode == "lost_with_result":
            path = self._write_result(assignment, outcome)
            return AssignedCheckResult(
                completed=False,
                uncertain=True,
                durable_result_found=True,
                durable_result_status=outcome,
                expected_result_path=str(path.relative_to(self.repo)).replace("\\", "/"),
                executor=assignment.get("executor"),
                required_check_id=assignment.get("required_check_id"),
                raw_output="response lost; durable result exists",
                started=True,
                exit_code=None,
                fresh_session_id=f"mock-exec-{self.executor_calls}",
            )

        path = self._write_result(assignment, outcome)
        return AssignedCheckResult(
            completed=True,
            uncertain=False,
            durable_result_found=True,
            durable_result_status=outcome,
            expected_result_path=str(path.relative_to(self.repo)).replace("\\", "/"),
            executor=assignment.get("executor"),
            required_check_id=assignment.get("required_check_id"),
            raw_output=f"STATUS: {outcome}",
            started=True,
            exit_code=0 if outcome == "PASS" else 1,
            fresh_session_id=f"mock-exec-{self.executor_calls}",
        )


_HARNESSES: dict[str, VerificationMockHarness] = {}


def get_mock_harness(
    workflow_id: str,
    *,
    scenario: str,
    change: str,
    repo: Path | str,
) -> VerificationMockHarness:
    harness = _HARNESSES.get(workflow_id)
    if harness is None:
        harness = VerificationMockHarness.from_scenario(scenario, change=change, repo=repo)
        _HARNESSES[workflow_id] = harness
    return harness


def clear_mock_harnesses() -> None:
    _HARNESSES.clear()
