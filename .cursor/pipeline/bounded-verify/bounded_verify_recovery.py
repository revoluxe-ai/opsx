# -*- coding: utf-8 -*-
"""Evidence-bound FAIL recovery authority."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_atomic import atomic_publish_json, read_canonical_json
from bounded_verify_ledger import (
    BoundedVerifyLedgerError,
    LEDGER_INVALID,
    _scheduler_lock,
    _validate_ledger_document,
    ledger_carrier_path,
    read_ledger,
    scheduler_lock_path,
)
from bounded_verify_plan import read_plan
from bounded_verify_plan_digest import frozen_content_digest, live_content_digest
from bounded_verify_result import find_results_for_assignment, ExpectedAssignment

REPO_ROOT = Path(__file__).resolve().parents[3]

RECOVERY_NOT_FAIL_STATE = "RECOVERY_NOT_FAIL_STATE"
RECOVERY_CHECK_MISMATCH = "RECOVERY_CHECK_MISMATCH"
RECOVERY_NO_FAIL_RESULT = "RECOVERY_NO_FAIL_RESULT"
RECOVERY_INPUT_DRIFT = "RECOVERY_INPUT_DRIFT"
RECOVERY_CONVERGENCE_EXCEEDED = "RECOVERY_CONVERGENCE_EXCEEDED"
RECOVERY_CROSS_PLAN = "RECOVERY_CROSS_PLAN"
MAX_RECOVERIES_PER_CHECK = 5
TECHNICAL_RETRY_PREFIX = "TECHNICAL_RETRY:"


@dataclass(frozen=True)
class RecoveryAuthorization:
    """Append-only recovery record."""

    recovery_id: str
    finding_id: str
    check_id: str
    prior_result_id: str
    prior_execution_id: str
    plan_id: str
    plan_fingerprint: str
    recovery_generation: int
    corrected_input_digest: str


def recovery_log_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    return (
        root
        / "openspec"
        / "changes"
        / change_id
        / "bounded-verify"
        / "recovery-log.json"
    )


def _finding_id(causal_finding: str) -> str:
    return hashlib.sha256(causal_finding.strip().encode("utf-8")).hexdigest()


def _prior_fail_result(
    change_id: str,
    check_id: str,
    plan_fingerprint: str,
    *,
    root: Path,
    ledger_revision: int | None = None,
) -> dict[str, Any]:
    from bounded_verify_result import results_dir

    rdir = results_dir(change_id, root=root)
    if not rdir.is_dir():
        raise BoundedVerifyLedgerError(RECOVERY_NO_FAIL_RESULT, "no results directory")
    candidates: list[dict[str, Any]] = []
    for path in sorted(rdir.glob("*.json")):
        doc = read_canonical_json(path)
        if not isinstance(doc, dict):
            continue
        if (
            doc.get("check_id") == check_id
            and doc.get("result_status") == "FAIL"
            and str(doc.get("plan_fingerprint", "")) == plan_fingerprint
        ):
            if ledger_revision is not None and int(doc.get("ledger_revision", -1)) != ledger_revision:
                continue
            candidates.append(doc)
    if not candidates:
        raise BoundedVerifyLedgerError(
            RECOVERY_NO_FAIL_RESULT,
            "no FAIL result evidence for check",
        )
    chosen = max(
        candidates,
        key=lambda d: (int(d.get("ledger_revision", 0)), int(d.get("assignment_epoch", 0))),
    )
    assignment = ExpectedAssignment(
        check_id=str(chosen["check_id"]),
        assignment_epoch=int(chosen["assignment_epoch"]),
        ledger_revision=int(chosen["ledger_revision"]),
        plan_fingerprint=str(chosen["plan_fingerprint"]),
    )
    bound = find_results_for_assignment(change_id, assignment, root=root)
    fails = [b for b in bound if str(b.get("result_status")) == "FAIL"]
    if len(fails) != 1 or str(fails[0].get("result_id")) != str(chosen["result_id"]):
        raise BoundedVerifyLedgerError(
            RECOVERY_NO_FAIL_RESULT,
            "FAIL result not uniquely bound to assignment identity",
        )
    return chosen


def authorize_check_recovery(
    change_id: str,
    check_id: str,
    *,
    causal_finding: str,
    root: Path = REPO_ROOT,
    technical_retry: bool = False,
) -> RecoveryAuthorization:
    """FAIL → PENDING only with verified FAIL evidence and input identity."""
    finding = causal_finding.strip()
    if not finding:
        raise BoundedVerifyLedgerError(RECOVERY_CHECK_MISMATCH, "causal_finding required")
    if technical_retry and not finding.startswith(TECHNICAL_RETRY_PREFIX):
        raise BoundedVerifyLedgerError(
            RECOVERY_CHECK_MISMATCH,
            "technical_retry requires TECHNICAL_RETRY: prefix",
        )

    lock_path = scheduler_lock_path(change_id, root=root)
    with _scheduler_lock(lock_path):
        plan = read_plan(change_id, root=root)
        plan_id = str(plan["plan_id"])
        plan_fp = str(plan["plan_fingerprint"])
        frozen = frozen_content_digest(plan)
        live = live_content_digest(change_id, plan, root=root)

        ledger = deepcopy(read_ledger(change_id, root=root))
        if str(ledger.get("plan_id", "")) != plan_id:
            raise BoundedVerifyLedgerError(RECOVERY_CROSS_PLAN, "ledger plan_id mismatch")

        target = None
        for entry in ledger["entries"]:
            if str(entry["check_id"]) == check_id:
                target = entry
                break
        if target is None:
            raise BoundedVerifyLedgerError(RECOVERY_CHECK_MISMATCH, "check_id not in ledger")
        if target.get("state") != "FAIL":
            raise BoundedVerifyLedgerError(
                RECOVERY_NOT_FAIL_STATE,
                f"recovery requires FAIL state, got {target.get('state')}",
            )

        prior = _prior_fail_result(change_id, check_id, plan_fp, root=root)
        prior_assignment = ExpectedAssignment(
            check_id=str(prior["check_id"]),
            assignment_epoch=int(prior["assignment_epoch"]),
            ledger_revision=int(prior["ledger_revision"]),
            plan_fingerprint=str(prior["plan_fingerprint"]),
        )
        bound = find_results_for_assignment(change_id, prior_assignment, root=root)
        if not bound or str(bound[0].get("result_id")) != str(prior["result_id"]):
            raise BoundedVerifyLedgerError(RECOVERY_NO_FAIL_RESULT, "FAIL result not bound")

        log_path = recovery_log_path(change_id, root=root)
        existing_log: list[Mapping[str, Any]] = []
        if log_path.is_file():
            raw_log = json.loads(log_path.read_text(encoding="utf-8"))
            if isinstance(raw_log, dict) and isinstance(raw_log.get("entries"), list):
                existing_log = list(raw_log["entries"])
            elif isinstance(raw_log, list):
                existing_log = raw_log
            else:
                raise BoundedVerifyLedgerError(LEDGER_INVALID, "recovery-log invalid")
        prior_findings = {
            str(r.get("finding_id", ""))
            for r in existing_log
            if str(r.get("check_id", "")) == check_id
        }
        fid = _finding_id(finding)
        if fid in prior_findings:
            raise BoundedVerifyLedgerError(
                RECOVERY_CONVERGENCE_EXCEEDED,
                "duplicate causal finding for check",
            )
        gen = sum(1 for r in existing_log if str(r.get("check_id", "")) == check_id) + 1
        if gen > MAX_RECOVERIES_PER_CHECK:
            raise BoundedVerifyLedgerError(
                RECOVERY_CONVERGENCE_EXCEEDED,
                f"max recoveries exceeded for {check_id}",
            )

        if not technical_retry:
            raise BoundedVerifyLedgerError(
                RECOVERY_INPUT_DRIFT,
                "only TECHNICAL_RETRY recovery is allowed on unchanged FROZEN plan; "
                "input changes require authorized plan supersession",
            )
        if live != frozen:
            raise BoundedVerifyLedgerError(
                RECOVERY_INPUT_DRIFT,
                "technical retry requires unchanged input identity",
            )
        corrected = frozen

        if len(corrected) != 64:
            raise BoundedVerifyLedgerError(RECOVERY_INPUT_DRIFT, "invalid live digest")

        recovery_id = str(uuid.uuid4())
        record = {
            "recovery_id": recovery_id,
            "finding_id": fid,
            "check_id": check_id,
            "prior_result_id": str(prior["result_id"]),
            "prior_execution_id": str(prior["execution_id"]),
            "plan_id": plan_id,
            "plan_fingerprint": plan_fp,
            "recovery_generation": gen,
            "frozen_content_digest": frozen,
            "live_content_digest": live,
            "corrected_input_digest": corrected,
            "causal_finding": finding,
            "technical_retry": technical_retry,
        }
        existing_log.append(record)
        pending = log_path.with_suffix(".pending.json")
        atomic_publish_json(pending, existing_log, validate=lambda _: None)
        if os.environ.get("BOUNDED_VERIFY_RECOVERY_CRASH") == "before_replace":
            raise BoundedVerifyLedgerError(LEDGER_INVALID, "crash hook before recovery-log replace")
        pending.replace(log_path)

        target["state"] = "PENDING"
        ledger["ledger_status"] = "ACTIVE"
        ledger["current_assignment"] = None
        ledger["ledger_revision"] = int(ledger["ledger_revision"]) + 1

        def _validate(payload: Mapping[str, Any]) -> None:
            _validate_ledger_document(payload)

        atomic_publish_json(ledger_carrier_path(change_id, root=root), ledger, validate=_validate)

    return RecoveryAuthorization(
        recovery_id=recovery_id,
        finding_id=fid,
        check_id=check_id,
        prior_result_id=str(prior["result_id"]),
        prior_execution_id=str(prior["execution_id"]),
        plan_id=plan_id,
        plan_fingerprint=plan_fp,
        recovery_generation=gen,
        corrected_input_digest=corrected,
    )
