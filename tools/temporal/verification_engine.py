"""OPSX verification execution helpers for Temporal sequencing.

Temporal owns sequencing only. /opsx-next-check remains the sole scheduler;
bounded executors remain the sole BOUNDED_CHECK_RESULT writers.

VERIFICATION_SESSION_DEPENDENCY: NONE
Durable plan/bindings/ledger/result are authority; chat history is not.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

VERIFICATION_LANE_ID = "verification"

NEXT_CHECK_PROMPT = "/opsx-next-check change={change}"

TERMINAL_SUCCESS = "ALL_REQUIRED_CHECKS_PASS"
TEMPORAL_COMPLETE = "VERIFICATION_EXECUTION_COMPLETE"
TERMINAL_FAIL = "VERIFICATION_CHECK_FAILED"
TERMINAL_BLOCKED_CHECK = "VERIFICATION_CHECK_BLOCKED"
TERMINAL_UNCERTAIN = "EXECUTOR_COMPLETION_UNCERTAIN"

NEXT_CHECK_BLOCK_STATUSES = frozenset(
    {
        "BLOCKED_SNAPSHOT_DRIFT",
        "CONTRACT_ERROR",
        "BLOCKED",
        "BLOCKED_EXECUTOR_GAP",
        "SNAPSHOT_CONTRACT_VERSION",
        "BLOCKED_SNAPSHOT_DRIFT",
    }
)

STATUS_LINE_RE = re.compile(r"^STATUS:\s*(\S+)", re.MULTILINE)
PROGRESS_RE = re.compile(r"^PROGRESS:\s*(\d+)\s*/\s*(\d+)", re.MULTILINE)


@dataclass
class NextCheckResult:
    completed: bool
    status: str
    progress_pass: int
    progress_total: int
    current_required_check_id: str | None
    assignment_epoch: int | None
    execute_assignment: dict[str, Any] | None
    current_last_result: str | None
    block_reason: str | None
    plan_id: str | None
    ledger_path: str | None
    raw_output: str
    evidence_missing: bool = False
    evidence_invalid: bool = False
    # Precomputed in classify_next_check (activity side). Workflows must not
    # touch the filesystem to decide smoke-ack reruns.
    rerun_blocked_smoke_ack: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AssignedCheckResult:
    completed: bool
    uncertain: bool
    durable_result_found: bool
    durable_result_status: str | None
    expected_result_path: str | None
    executor: str | None
    required_check_id: str | None
    raw_output: str
    started: bool = True
    exit_code: int | None = None
    fresh_session_id: str | None = None
    owner_chrome_focus_gate_pending: bool = False
    owner_chrome_focus_gate: dict[str, Any] | None = None
    owner_browser_check_release: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BOUNDED_LEDGER_SCHEMA = "bounded-verify-ledger-v1"
FULL_REQUIRED_ATTESTATION = "FULL_REQUIRED_VERIFICATION_COMPLETE"
AGENT_SUCCESS_TOKENS = frozenset(
    {
        TERMINAL_SUCCESS,
        "ALL_REQUIRED_CHECKS_PASS",
        TEMPORAL_COMPLETE,
        "VERIFICATION_EXECUTION_COMPLETE",
    }
)


def change_verify_dir(repo: Path | str, change: str) -> Path:
    return Path(repo) / "openspec" / "changes" / change / "verify"


def bounded_verify_dir(repo: Path | str, change: str) -> Path:
    return Path(repo) / "openspec" / "changes" / change / "bounded-verify"


def ledger_path(repo: Path | str, change: str) -> Path:
    """Prefer bounded-verify ledger; fall back to legacy verify carrier for mocks."""
    bounded = bounded_verify_dir(repo, change) / "ledger.json"
    if bounded.is_file():
        return bounded
    return change_verify_dir(repo, change) / "VERIFICATION-LEDGER.json"


def load_ledger(repo: Path | str, change: str) -> dict[str, Any]:
    path = ledger_path(repo, change)
    if not path.is_file():
        raise FileNotFoundError(f"bounded-verify ledger missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_bounded_verify_import(repo: Path | str) -> None:
    bv = Path(repo) / ".cursor" / "pipeline" / "bounded-verify"
    entry = str(bv)
    if bv.is_dir() and entry not in sys.path:
        sys.path.insert(0, entry)


def load_plan(repo: Path | str, change: str) -> dict[str, Any] | None:
    """Load plan.json without fingerprint validation (legacy callers)."""
    path = plan_path(repo, change)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_validated_frozen_plan(
    repo: Path | str, change: str
) -> tuple[dict[str, Any] | None, str | None]:
    """FROZEN plan with recalculated fingerprint; fail-closed on tamper/absence."""
    plan_file = plan_path(repo, change)
    if not plan_file.is_file():
        return None, "plan_missing"
    _ensure_bounded_verify_import(repo)
    try:
        from bounded_verify_plan import BoundedVerifyPlanError, read_plan

        return read_plan(change, root=Path(repo)), None
    except BoundedVerifyPlanError as exc:
        code = str(exc.code).lower()
        if "fingerprint" in exc.message.lower() or code == "plan_invalid":
            return None, "plan_fingerprint_tampered"
        if code == "plan_not_found":
            return None, "plan_missing"
        return None, "plan_invalid"
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None, "plan_invalid"


def is_bounded_ledger(ledger: dict[str, Any]) -> bool:
    if str(ledger.get("schema_version", "")) == BOUNDED_LEDGER_SCHEMA:
        return True
    return "entries" in ledger and not ledger.get("checks") and not ledger.get(
        "required_checks"
    )


def ledger_rows(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(ledger.get("entries"), list):
        return list(ledger["entries"])
    return list(ledger.get("checks") or ledger.get("required_checks") or [])


def find_row(ledger: dict[str, Any], required_check_id: str | None) -> dict[str, Any] | None:
    if not required_check_id:
        return None
    for row in ledger_rows(ledger):
        row_id = row.get("required_check_id") or row.get("check_id")
        if row_id == required_check_id:
            return row
    return None


def current_required_check_id(ledger: dict[str, Any]) -> str | None:
    if is_bounded_ledger(ledger):
        assignment = ledger.get("current_assignment")
        if isinstance(assignment, dict) and assignment.get("check_id"):
            return str(assignment["check_id"])
        for row in ledger_rows(ledger):
            if row.get("state") == "CURRENT":
                return str(row.get("check_id"))
        return None
    return ledger.get("current_required_check_id")


def progress_from_ledger(ledger: dict[str, Any]) -> tuple[int, int]:
    rows = ledger_rows(ledger)
    total = int(ledger.get("required_check_count") or len(rows))
    passed = sum(1 for row in rows if row.get("state") == "PASS")
    return passed, total


def ledger_has_fail(ledger: dict[str, Any]) -> bool:
    return any(row.get("state") == "FAIL" for row in ledger_rows(ledger))


def _load_ledger_document(repo: Path | str, change: str) -> tuple[dict[str, Any] | None, str | None]:
    """Load bounded ledger; None + reason when missing/invalid."""
    path = ledger_path(repo, change)
    if not path.is_file():
        return None, "ledger_missing"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "ledger_invalid"
    if not isinstance(parsed, dict):
        return None, "ledger_invalid"
    return parsed, None


def _required_check_rows(plan: Mapping[str, Any]) -> list[tuple[int, str]]:
    raw = plan.get("required_checks")
    if not isinstance(raw, list):
        return []
    rows: list[tuple[int, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append((int(item.get("order", 0)), str(item.get("check_id", "")).strip()))
    rows.sort(key=lambda pair: pair[0])
    return rows


def _ledger_entry_rows(ledger: Mapping[str, Any]) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for item in ledger_rows(dict(ledger)):
        if not isinstance(item, dict):
            continue
        rows.append((int(item.get("order", 0)), str(item.get("check_id", "")).strip()))
    rows.sort(key=lambda pair: pair[0])
    return rows


def _terminal_validate_bound_results(
    repo: Path | str,
    change: str,
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> str | None:
    """Each PASS ledger row must reference the exact accepted result artifact."""
    _ensure_bounded_verify_import(repo)
    try:
        from bounded_verify_plan_digest import content_digest_required_for_drain
        from bounded_verify_result import (
            BoundedVerifyResultError,
            ExpectedAssignment,
            read_result,
            validate_result_for_drain,
        )
    except ImportError:
        return "terminal_evidence_validator_unavailable"

    fp = str(plan.get("plan_fingerprint", ""))
    content_digest = content_digest_required_for_drain(plan)
    seen_result_ids: set[str] = set()
    root = Path(repo)

    for entry in ledger_rows(dict(ledger)):
        if entry.get("state") != "PASS":
            continue
        check_id = str(entry.get("check_id", ""))
        bound_rid = entry.get("accepted_result_id")
        if not bound_rid:
            return "ledger_pass_result_binding_missing"
        bound_rid_s = str(bound_rid)
        if bound_rid_s in seen_result_ids:
            return "duplicate_accepted_result_binding"
        seen_result_ids.add(bound_rid_s)

        try:
            result_doc = read_result(change, bound_rid_s, root=root)
        except BoundedVerifyResultError:
            return "accepted_result_invalid_or_missing"

        if str(result_doc.get("result_status", "")) != "PASS":
            return "accepted_result_not_pass"

        bound_epoch = entry.get("accepted_assignment_epoch")
        bound_rev = entry.get("accepted_ledger_revision")
        bound_exec = entry.get("accepted_execution_id")
        if bound_epoch is None or bound_rev is None or not bound_exec:
            return "ledger_pass_binding_incomplete"
        if str(result_doc.get("result_id", "")) != bound_rid_s:
            return "accepted_result_id_mismatch"
        if str(result_doc.get("execution_id", "")) != str(bound_exec):
            return "accepted_execution_id_mismatch"
        if int(result_doc.get("assignment_epoch", -1)) != int(bound_epoch):
            return "accepted_assignment_epoch_mismatch"
        if int(result_doc.get("ledger_revision", -1)) != int(bound_rev):
            return "accepted_ledger_revision_mismatch"

        expected = ExpectedAssignment(
            check_id=check_id,
            assignment_epoch=int(bound_epoch),
            ledger_revision=int(bound_rev),
            plan_fingerprint=fp,
            content_digest=content_digest,
        )
        decision = validate_result_for_drain(result_doc, expected)
        if not decision.allowed:
            return f"accepted_result_drain_rejected:{decision.code}"

        if content_digest is not None:
            entry_digest = entry.get("accepted_content_digest")
            if str(entry_digest or "") != content_digest:
                return "accepted_content_digest_mismatch"
            if str(result_doc.get("content_digest", "")) != content_digest:
                return "result_content_digest_mismatch"

    if not seen_result_ids:
        return "ledger_pass_result_binding_missing"
    return None


def _terminal_authoritative_freshness(
    plan: Mapping[str, Any],
    change: str,
    repo: Path | str,
) -> str | None:
    """Same freshness predicates as schedule_write before TERMINAL_SUCCESS."""
    _ensure_bounded_verify_import(repo)
    try:
        from bounded_verify_plan import BoundedVerifyPlanError
        from bounded_verify_s5_obligation_applicability import (
            assert_conditional_applicability_current,
        )
        from bounded_verify_snapshot import assert_snapshot_fresh

        assert_snapshot_fresh(plan, change, root=Path(repo))
        assert_conditional_applicability_current(plan, change, root=Path(repo))
    except BoundedVerifyPlanError as exc:
        code = str(exc.code)
        if code in (
            "BLOCKED_SNAPSHOT_DRIFT",
            "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
        ):
            return code
        return "terminal_freshness_rejected"
    except Exception:
        return "terminal_freshness_rejected"
    return None


def bounded_terminal_success_authorized(
    *,
    repo: Path | str,
    change: str,
    ledger: Mapping[str, Any],
    plan: Mapping[str, Any] | None,
) -> str | None:
    """Return block_reason when terminal SUCCESS is not authorized; else None."""
    if not is_bounded_ledger(dict(ledger)):
        return "not_bounded_ledger"
    if plan is None:
        return "plan_missing"
    if str(plan.get("plan_status", "")) != "FROZEN":
        return "plan_not_frozen"
    if str(ledger.get("plan_fingerprint", "")) != str(plan.get("plan_fingerprint", "")):
        return "plan_fingerprint_mismatch"
    if str(ledger.get("plan_id", "")) != str(plan.get("plan_id", "")):
        return "plan_id_mismatch"
    if str(ledger.get("schema_version", "")) != BOUNDED_LEDGER_SCHEMA:
        return "ledger_schema_invalid"
    plan_rows = _required_check_rows(plan)
    if not plan_rows or any(not cid for _, cid in plan_rows):
        return "plan_required_checks_invalid"
    ledger_rows_sorted = _ledger_entry_rows(ledger)
    if plan_rows != ledger_rows_sorted:
        return "ledger_plan_check_identity_mismatch"
    plan_ids = [cid for _, cid in plan_rows]
    if len(plan_ids) != len(set(plan_ids)):
        return "ledger_duplicate_check_id"
    rows = ledger_rows(dict(ledger))
    if not rows:
        return "ledger_entries_empty"
    if any(row.get("state") != "PASS" for row in rows):
        return "ledger_entries_not_all_pass"
    if str(ledger.get("ledger_status", "")) != "COMPLETE":
        return "ledger_not_complete"
    binding_block = _terminal_validate_bound_results(repo, change, plan, ledger)
    if binding_block is not None:
        return binding_block
    freshness_block = _terminal_authoritative_freshness(plan, change, repo)
    if freshness_block is not None:
        return freshness_block
    attestation = str(ledger.get("completion_attestation", ""))
    profile = plan.get("verification_profile")
    if attestation == FULL_REQUIRED_ATTESTATION:
        if profile != "full_required" or str(plan.get("schema_version", "")) != "bounded-verify-plan-v2":
            return "forged_full_required_attestation"
    if profile == "full_required" and attestation != FULL_REQUIRED_ATTESTATION:
        return "full_required_attestation_missing"
    if profile in ("static_fast", "static_full", "bounded_minimal") and attestation == FULL_REQUIRED_ATTESTATION:
        return "static_profile_cannot_claim_full_required"
    return None


def build_bounded_execute_assignment(
    *,
    repo: Path | str,
    change: str,
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    check_id: str,
    assignment_epoch: int,
) -> dict[str, Any]:
    """In-memory CHECK_ASSIGNMENT for TARGET bounded-verify (not persisted)."""
    snap = plan.get("implementation_snapshot") or {}
    digest = str(snap.get("content_digest", ""))
    fingerprint = str(plan.get("plan_fingerprint", ""))
    return {
        "bounded_verify_target": True,
        "executor": "bounded-executor",
        "executor_mode": "bounded",
        "change_id": change,
        "plan_id": str(plan["plan_id"]),
        "required_check_id": check_id,
        "check_id": check_id,
        "target_id": check_id,
        "capability_id": check_id,
        "binding_fingerprint": fingerprint,
        "verification_frontier_hash": fingerprint,
        "bindings_path": "",
        "expected_result_path": "",
        "assignment_epoch": assignment_epoch,
        "ledger_revision": int(ledger["ledger_revision"]),
        "implementation_snapshot": {
            "provenance_head": "",
            "content_digest": digest,
            "content_paths": [],
        },
    }


def build_next_check_prompt(change: str) -> str:
    return NEXT_CHECK_PROMPT.format(change=change.strip())


_REQUIRED_ASSIGNMENT_KEYS = (
    "change_id",
    "plan_id",
    "required_check_id",
    "target_id",
    "capability_id",
    "executor",
    "executor_mode",
    "binding_fingerprint",
    "verification_frontier_hash",
    "bindings_path",
    "expected_result_path",
    "assignment_epoch",
    "implementation_snapshot",
)


def validate_assignment_for_execute(assignment: dict[str, Any] | None) -> list[str]:
    """Return missing CHECK_ASSIGNMENT fields; empty list means dispatch-safe."""
    if not assignment or not isinstance(assignment, dict):
        return ["assignment"]
    if assignment.get("bounded_verify_target"):
        missing_bounded: list[str] = []
        for key in (
            "change_id",
            "plan_id",
            "required_check_id",
            "assignment_epoch",
            "ledger_revision",
        ):
            if key not in assignment or assignment[key] in (None, ""):
                missing_bounded.append(key)
        return missing_bounded
    missing: list[str] = []
    for key in _REQUIRED_ASSIGNMENT_KEYS:
        if key not in assignment or assignment[key] in (None, ""):
            missing.append(key)
            continue
        if key == "implementation_snapshot":
            snap = assignment.get("implementation_snapshot")
            if not isinstance(snap, dict):
                missing.append("implementation_snapshot")
                continue
            for snap_key in ("provenance_head", "content_digest", "content_paths"):
                if snap_key not in snap or snap[snap_key] in (None, ""):
                    missing.append(f"implementation_snapshot.{snap_key}")
            if not isinstance(snap.get("content_paths"), list):
                missing.append("implementation_snapshot.content_paths")
    return missing


def browser_check_cloud_dispatch_lines(assignment: dict[str, Any]) -> list[str]:
    """Cloud/Temporal nested Composer dispatch guardrails for opsx-browser-check."""
    snap = assignment.get("implementation_snapshot") or {}
    return [
        "",
        "CLOUD_BROWSER_DISPATCH (HARD — Temporal/Composer nested executor):",
        "  Durable CHECK_ASSIGNMENT + on-disk BOUNDED_CHECK_RESULT are authority.",
        "  Nested agent chat STATUS alone is NOT verification PASS.",
        f"  DURABLE_RESULT_PATH: {assignment.get('expected_result_path')}",
        "  CLOUD_BROWSER_OWNERSHIP: before runnable steps prove browser runtime:",
        "    cursor-ide-browser namespaceStatus=ready after bootstrap ladder, OR",
        "    localhost user-chrome-devtools CDP fallback when MCP bootstrap fails.",
        "  Do NOT wait for Owner GO / Chrome-focus ack (retired for /tempo-verification).",
        "  Missing/unready browser MCP/CDP/lifecycle tools → BROWSER_CHECK_BLOCKED",
        "  (never PASS-by-absence; never infer PASS from agent stdout).",
        "  L2_trigger: require native target-document visibility proof",
        "  visible→hidden→visible (read-only observe); lifecycle CDP success alone",
        "  is insufficient; synthetic visibility events forbidden for PASS.",
        "  Post-active network_after_ui / terminal observable must be causal",
        "  (timestamped after native visible restoration).",
        "",
        "SCHEDULER_IDENTITY_BOUND (echo on durable result findings):",
        f"  plan_id={assignment.get('plan_id')}",
        f"  required_check_id={assignment.get('required_check_id')}",
        f"  assignment_epoch={assignment.get('assignment_epoch')}",
        f"  binding_fingerprint={assignment.get('binding_fingerprint')}",
        f"  verification_frontier_hash={assignment.get('verification_frontier_hash')}",
        f"  expected_result_path={assignment.get('expected_result_path')}",
        f"  implementation_snapshot.provenance_head={snap.get('provenance_head', '')}",
        f"  implementation_snapshot.content_digest={snap.get('content_digest', '')}",
    ]


def build_execute_prompt(assignment: dict[str, Any], *, repo: Path | str | None = None) -> str:
    """Build the exact bounded executor prompt from durable CHECK_ASSIGNMENT."""
    if assignment.get("bounded_verify_target"):
        check_id = str(assignment.get("check_id") or assignment["required_check_id"])
        lines = [
            "/bounded-executor",
            "mode=bounded",
            f"change={assignment['change_id']}",
            f"check_id={check_id}",
            f"assignment_epoch={assignment['assignment_epoch']}",
            f"ledger_revision={assignment['ledger_revision']}",
        ]
        return "\n".join(lines)
    executor = str(assignment["executor"]).lstrip("/")
    mode = assignment.get("executor_mode") or "bounded"
    snap = assignment.get("implementation_snapshot") or {}
    lines = [
        f"/{executor}",
        f"mode={mode}",
        f"change={assignment['change_id']}",
        f"plan_id={assignment['plan_id']}",
        f"required_check_id={assignment['required_check_id']}",
        f"target_id={assignment['target_id']}",
        f"capability_id={assignment['capability_id']}",
        f"executor={executor}",
        f"executor_mode={mode}",
        f"binding_fingerprint={assignment['binding_fingerprint']}",
        f"verification_frontier_hash={assignment['verification_frontier_hash']}",
        f"implementation_snapshot.provenance_head={snap.get('provenance_head', '')}",
        f"implementation_snapshot.content_digest={snap.get('content_digest', '')}",
        f"implementation_snapshot.content_paths_count={len(snap.get('content_paths') or [])}",
        f"bindings_path={assignment['bindings_path']}",
        f"expected_result_path={assignment['expected_result_path']}",
        f"assignment_epoch={assignment['assignment_epoch']}",
    ]
    browser_check = False
    if repo is not None:
        try:
            from owner_browser_check_gate import (
                browser_check_post_prompt_lines,
                is_browser_check_assignment,
            )

            browser_check = is_browser_check_assignment(assignment)
            if browser_check:
                lines.extend(browser_check_cloud_dispatch_lines(assignment))
                lines.extend(
                    browser_check_post_prompt_lines(
                        repo=Path(repo),
                        change=str(assignment["change_id"]),
                    )
                )
        except ImportError:
            pass
    return "\n".join(lines)


def parse_status_from_agent_output(raw: str) -> str | None:
    match = STATUS_LINE_RE.search(raw or "")
    return match.group(1) if match else None


def parse_progress_from_agent_output(raw: str) -> tuple[int | None, int | None]:
    match = PROGRESS_RE.search(raw or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def classify_next_check(
    *,
    repo: Path | str,
    change: str,
    raw_output: str,
    completed: bool,
    agent_exit_code: int | None = None,
) -> NextCheckResult:
    """Prefer durable ledger; use agent output only as UX/status hint."""
    path = ledger_path(repo, change)
    ledger, ledger_load_reason = _load_ledger_document(repo, change)

    agent_status = parse_status_from_agent_output(raw_output) or ""
    progress_hint = parse_progress_from_agent_output(raw_output)

    if not completed and agent_exit_code not in (0, None):
        # Technical failure — caller may retry; do not invent semantic status.
        return NextCheckResult(
            completed=False,
            status="NEXT_CHECK_TECHNICAL_FAILURE",
            progress_pass=progress_hint[0] or 0,
            progress_total=progress_hint[1] or 0,
            current_required_check_id=None,
            assignment_epoch=None,
            execute_assignment=None,
            current_last_result=None,
            block_reason="cursor_agent_transport_or_exit",
            plan_id=ledger.get("plan_id") if ledger else None,
            ledger_path=str(path) if path.is_file() else None,
            raw_output=raw_output,
        )
    if ledger is None:
        return NextCheckResult(
            completed=True,
            status="BLOCKED",
            progress_pass=progress_hint[0] or 0,
            progress_total=progress_hint[1] or 0,
            current_required_check_id=None,
            assignment_epoch=None,
            execute_assignment=None,
            current_last_result=None,
            block_reason=ledger_load_reason or "ledger_missing_after_next_check",
            plan_id=None,
            ledger_path=str(path) if path.is_file() else None,
            raw_output=raw_output,
        )

    progress_pass, progress_total = progress_from_ledger(ledger)
    assignment = ledger.get("current_assignment")
    current_id = ledger.get("current_required_check_id")
    if current_id is None:
        assignment = ledger.get("current_assignment")
        if isinstance(assignment, dict):
            current_id = assignment.get("check_id")
    row = find_row(ledger, current_id)
    last_result = row.get("last_result") if row else None
    if is_bounded_ledger(ledger) and row and row.get("state") == "FAIL":
        last_result = "FAIL"

    if ledger_has_fail(ledger):
        status = TERMINAL_FAIL
    elif ledger.get("ledger_status") == "COMPLETE" or (
        progress_pass == progress_total and progress_total > 0 and assignment is None
    ):
        plan, plan_reason = load_validated_frozen_plan(repo, change)
        if plan is None:
            status = "BLOCKED"
            block_reason = plan_reason or "plan_missing"
        else:
            block = bounded_terminal_success_authorized(
                repo=repo, change=change, ledger=ledger, plan=plan
            )
            if block is not None:
                status = "BLOCKED"
                block_reason = block
            else:
                status = TERMINAL_SUCCESS
    elif agent_status in AGENT_SUCCESS_TOKENS:
        status = "BLOCKED"
        block_reason = "agent_status_cannot_authorize_success"
    elif agent_status in NEXT_CHECK_BLOCK_STATUSES:
        status = agent_status
    elif agent_status == "BLOCKED_EXECUTOR_GAP":
        status = "BLOCKED_EXECUTOR_GAP"
    elif assignment and current_id:
        status = "CHECK_REQUIRED"
        if is_bounded_ledger(ledger):
            plan, plan_reason = load_validated_frozen_plan(repo, change)
            if plan is None:
                status = "BLOCKED"
                block_reason = plan_reason or "plan_missing_for_bounded_assignment"
            else:
                epoch = int(assignment.get("assignment_epoch") or 0)
                execute_assignment = build_bounded_execute_assignment(
                    repo=repo,
                    change=change,
                    plan=plan,
                    ledger=ledger,
                    check_id=current_id,
                    assignment_epoch=epoch,
                )
        else:
            execute_assignment = assignment if isinstance(assignment, dict) else None
    elif agent_status and agent_status not in AGENT_SUCCESS_TOKENS:
        status = agent_status
    else:
        status = "BLOCKED"

    evidence_missing = "EVIDENCE_MISSING" in (raw_output or "")
    evidence_invalid = "EVIDENCE_INVALID" in (raw_output or "")
    if status == "CHECK_REQUIRED" and (evidence_missing or evidence_invalid):
        # Still CHECK_REQUIRED per next-check; surface flags for workflow.
        pass

    if block_reason is None and (
        status in NEXT_CHECK_BLOCK_STATUSES or status == "BLOCKED_EXECUTOR_GAP"
    ):
        reason_match = re.search(r"^REASON:\s*(.+)$", raw_output or "", re.MULTILINE)
        block_reason = reason_match.group(1).strip() if reason_match else status

    if execute_assignment is None and status == "CHECK_REQUIRED" and isinstance(
        assignment, dict
    ):
        execute_assignment = assignment

    result = NextCheckResult(
        completed=True,
        status=status,
        progress_pass=progress_pass,
        progress_total=progress_total,
        current_required_check_id=current_id,
        assignment_epoch=(
            (execute_assignment or assignment or {}).get("assignment_epoch")
            if (execute_assignment or assignment)
            else None
        ),
        execute_assignment=execute_assignment,
        current_last_result=last_result,
        block_reason=block_reason,
        plan_id=ledger.get("plan_id"),
        ledger_path=str(path),
        raw_output=raw_output,
        evidence_missing=evidence_missing,
        evidence_invalid=evidence_invalid,
    )
    return replace(
        result,
        rerun_blocked_smoke_ack=should_rerun_blocked_after_smoke_ack(
            repo=repo, next_check=result
        ),
    )


def load_bounded_result(path: Path | str) -> dict[str, Any] | None:
    result_path = Path(path)
    if not result_path.is_file():
        return None
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("STATUS") not in ("PASS", "FAIL", "BLOCKED"):
        return None
    required = (
        "schema_version",
        "change_id",
        "plan_id",
        "required_check_id",
        "target_id",
        "capability_id",
        "executor",
        "executor_mode",
        "binding_fingerprint",
        "verification_frontier_hash",
        "implementation_snapshot",
        "STATUS",
    )
    if any(key not in data for key in required):
        return None
    return data


def inspect_expected_result(
    assignment: dict[str, Any],
    *,
    repo: Path | str | None = None,
) -> dict[str, Any] | None:
    if assignment.get("bounded_verify_target") and repo is not None:
        return _load_bounded_verify_result(assignment, repo=repo)
    expected = assignment.get("expected_result_path")
    if not expected:
        return None
    path = Path(expected)
    if not path.is_absolute() and repo is not None:
        path = Path(repo) / expected
    return load_bounded_result(path)


def _load_bounded_verify_result(
    assignment: Mapping[str, Any],
    *,
    repo: Path | str,
) -> dict[str, Any] | None:
    """Map TARGET result document to SOURCE-shaped status for Temporal."""
    change = str(assignment["change_id"])
    results_dir = bounded_verify_dir(repo, change) / "results"
    if not results_dir.is_dir():
        return None
    check_id = str(assignment.get("check_id") or assignment["required_check_id"])
    epoch = int(assignment["assignment_epoch"])
    revision = int(assignment["ledger_revision"])
    fingerprint = str(assignment.get("binding_fingerprint") or "")
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if (
            data.get("check_id") == check_id
            and int(data.get("assignment_epoch", -1)) == epoch
            and int(data.get("ledger_revision", -1)) == revision
            and str(data.get("plan_fingerprint", "")) == fingerprint
        ):
            status = str(data.get("result_status", ""))
            if status == "FAIL":
                mapped = "FAIL"
            elif status == "PASS":
                mapped = "PASS"
            else:
                return None
            return {
                "STATUS": mapped,
                "schema_version": data.get("schema_version"),
                "change_id": change,
                "plan_id": assignment.get("plan_id"),
                "required_check_id": check_id,
                "target_id": check_id,
                "capability_id": check_id,
                "binding_fingerprint": fingerprint,
                "verification_frontier_hash": fingerprint,
                "implementation_snapshot": assignment.get("implementation_snapshot"),
            }
    return None


def classify_after_executor(
    *,
    next_check: NextCheckResult,
) -> dict[str, Any] | None:
    """Return a terminal payload if reconcile says stop; else None to continue."""
    if next_check.status == TERMINAL_SUCCESS:
        return {
            "status": TERMINAL_SUCCESS,
            "temporal_status": TEMPORAL_COMPLETE,
            "progress_pass": next_check.progress_pass,
            "progress_total": next_check.progress_total,
            "plan_id": next_check.plan_id,
            "current_required_check_id": None,
            "next_action": f"/opsx-verify change=<change> mode=archive",
        }

    if next_check.status in NEXT_CHECK_BLOCK_STATUSES or next_check.status == "BLOCKED_EXECUTOR_GAP":
        return {
            "status": next_check.status,
            "progress_pass": next_check.progress_pass,
            "progress_total": next_check.progress_total,
            "plan_id": next_check.plan_id,
            "block_reason": next_check.block_reason,
            "current_required_check_id": next_check.current_required_check_id,
            "ledger_path": next_check.ledger_path,
        }

    if next_check.status == "CHECK_REQUIRED" and next_check.current_last_result == "FAIL":
        assignment = next_check.execute_assignment or {}
        return {
            "status": TERMINAL_FAIL,
            "progress_pass": next_check.progress_pass,
            "progress_total": next_check.progress_total,
            "plan_id": next_check.plan_id,
            "failed_required_check_id": next_check.current_required_check_id,
            "target_id": assignment.get("target_id"),
            "capability_id": assignment.get("capability_id"),
            "executor": assignment.get("executor"),
            "result_path": assignment.get("expected_result_path"),
            "result_status": "FAIL",
            "next_action": "Owner/process decides repair path",
        }

    if next_check.status == "CHECK_REQUIRED" and next_check.current_last_result == "BLOCKED":
        assignment = next_check.execute_assignment or {}
        return {
            "status": TERMINAL_BLOCKED_CHECK,
            "progress_pass": next_check.progress_pass,
            "progress_total": next_check.progress_total,
            "plan_id": next_check.plan_id,
            "blocked_required_check_id": next_check.current_required_check_id,
            "target_id": assignment.get("target_id"),
            "capability_id": assignment.get("capability_id"),
            "executor": assignment.get("executor"),
            "result_path": assignment.get("expected_result_path"),
            "result_status": "BLOCKED",
            "ledger_path": next_check.ledger_path,
        }

    if next_check.status == "CHECK_REQUIRED" and (
        next_check.evidence_invalid or next_check.evidence_missing
    ):
        # Candidate only. Callers MUST gate with should_stop_on_evidence_gap:
        # real /opsx-next-check writes EVIDENCE_MISSING on a *new* CURRENT
        # before that check's executor has run.
        return {
            "status": "EVIDENCE_INVALID" if next_check.evidence_invalid else "EVIDENCE_MISSING",
            "progress_pass": next_check.progress_pass,
            "progress_total": next_check.progress_total,
            "plan_id": next_check.plan_id,
            "current_required_check_id": next_check.current_required_check_id,
            "ledger_path": next_check.ledger_path,
            "result_path": (next_check.execute_assignment or {}).get("expected_result_path"),
        }

    return None


def _pre_smoke_ack_mtime(repo: Path, assignment: dict[str, Any]) -> float | None:
    """Latest mtime of focus-ack or go-ack for this assignment, if any."""
    required_check_id = str(assignment.get("required_check_id") or "")
    epoch = int(assignment.get("assignment_epoch") or 0)
    if not required_check_id or epoch <= 0:
        return None
    try:
        from owner_browser_check_gate import go_ack_path
        from owner_chrome_focus_gate import ack_path as focus_ack_path
    except ImportError:
        return None
    mtimes: list[float] = []
    for path in (
        focus_ack_path(repo, required_check_id=required_check_id, assignment_epoch=epoch),
        go_ack_path(repo, required_check_id=required_check_id, assignment_epoch=epoch),
    ):
        if path.is_file():
            mtimes.append(path.stat().st_mtime)
    return max(mtimes) if mtimes else None


def should_rerun_blocked_after_smoke_ack(
    *,
    repo: Path | str,
    next_check: NextCheckResult,
) -> bool:
    """True when Owner smoke ack is newer than prior BLOCKED evidence — force executor."""
    if next_check.status != "CHECK_REQUIRED":
        return False
    if next_check.current_last_result != "BLOCKED":
        return False
    assignment = next_check.execute_assignment
    if not assignment:
        return False
    try:
        from owner_browser_check_gate import is_browser_check_assignment
    except ImportError:
        return False
    if not is_browser_check_assignment(assignment):
        return False

    repo_path = Path(repo)
    ack_mtime = _pre_smoke_ack_mtime(repo_path, assignment)
    if ack_mtime is None:
        return False

    expected = assignment.get("expected_result_path")
    if not expected:
        return False
    result_path = Path(expected)
    if not result_path.is_absolute():
        result_path = repo_path / expected
    if not result_path.is_file():
        return True

    return ack_mtime >= result_path.stat().st_mtime


def should_stop_on_prior_fail_blocked(
    *,
    repo: Path | str | None,
    next_check: NextCheckResult,
) -> bool:
    """True when reconcile should terminal-stop on ledger last_result FAIL/BLOCKED."""
    if next_check.current_last_result not in ("FAIL", "BLOCKED"):
        return False
    if next_check.current_last_result == "FAIL":
        return True
    if next_check.rerun_blocked_smoke_ack:
        return False
    if repo is not None and should_rerun_blocked_after_smoke_ack(
        repo=repo, next_check=next_check
    ):
        return False
    return True


def workflow_stop_on_prior_fail_blocked(next_check: NextCheckResult) -> bool:
    """Sandbox-safe workflow gate using activity-precomputed smoke-ack rerun."""
    if next_check.current_last_result not in ("FAIL", "BLOCKED"):
        return False
    if next_check.current_last_result == "FAIL":
        return True
    return not next_check.rerun_blocked_smoke_ack


def should_stop_on_evidence_gap(
    next_check: NextCheckResult,
    last_executed_required_check_id: str | None,
) -> bool:
    """True only when missing/invalid evidence is for the check already executed.

    Fresh CHECK_REQUIRED for the next frozen check includes EVIDENCE_MISSING
    in next-check prose (no result file yet). That is assignment, not a stop.
    """
    if not (next_check.evidence_missing or next_check.evidence_invalid):
        return False
    if next_check.status != "CHECK_REQUIRED":
        return False
    current = next_check.current_required_check_id
    if not current or not last_executed_required_check_id:
        return False
    return current == last_executed_required_check_id
