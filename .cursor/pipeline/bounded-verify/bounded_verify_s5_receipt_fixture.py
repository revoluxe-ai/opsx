# -*- coding: utf-8 -*-
"""S5-A fixture receipt writer (isolated outputs only — not S5-C production receipts)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_atomic import canonical_json_bytes
from bounded_verify_s5_common import canonical_evidence_producer, require_frozen_plan_binding
from bounded_verify_s5_fixture_semantics import (
    FIXTURE_EVIDENCE_SCOPE,
    FIXTURE_STATUS_BLOCKED,
    FIXTURE_STATUS_FAIL,
    FIXTURE_STATUS_PASS,
    is_production_s5_evidence,
)
from bounded_verify_s5_hermetic_runtime import (
    HermeticBoundaryError,
    assert_hermetic_s5_workspace,
    assert_receipt_path_contained,
    validate_s5_path_component,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

RECEIPT_ALREADY_EXISTS = "S5_FIXTURE_RECEIPT_ALREADY_EXISTS"
RECEIPT_IMMUTABLE_CONFLICT = "S5_FIXTURE_RECEIPT_IMMUTABLE_CONFLICT"


class S5FixtureReceiptError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class FixtureReceiptRecord:
    change_id: str
    obligation_id: str
    execution_id: str
    carrier_path: Path


def fixture_receipt_dir(change_id: str, obligation_id: str, *, root: Path) -> Path:
    safe_change = validate_s5_path_component(change_id, "change_id")
    safe_obligation = validate_s5_path_component(obligation_id, "obligation_id")
    return (
        root
        / "openspec/changes"
        / safe_change
        / "bounded-verify"
        / "fixture-receipts"
        / safe_obligation
    )


def fixture_receipt_path(
    change_id: str,
    obligation_id: str,
    execution_id: str,
    *,
    root: Path,
) -> Path:
    safe_exec = validate_s5_path_component(execution_id, "execution_id")
    path = fixture_receipt_dir(change_id, obligation_id, root=root) / f"{safe_exec}.json"
    return assert_receipt_path_contained(path, workspace_root=root)


def publish_fixture_receipt(
    change_id: str,
    obligation_id: str,
    *,
    execution_id: str,
    plan_id: str,
    plan_fingerprint: str,
    input_digest: str,
    policy_identity: Mapping[str, str],
    evidence_contract: str,
    status: str,
    check_evidence: Mapping[str, Any],
    root: Path = REPO_ROOT,
) -> FixtureReceiptRecord:
    """O_CREAT publish; same execution_id + identical body is idempotent."""
    try:
        assert_hermetic_s5_workspace(root)
        validate_s5_path_component(change_id, "change_id")
        validate_s5_path_component(obligation_id, "obligation_id")
        validate_s5_path_component(execution_id, "execution_id")
    except HermeticBoundaryError as exc:
        raise S5FixtureReceiptError(exc.code, exc.message) from exc
    try:
        canonical_plan_id, canonical_fingerprint = require_frozen_plan_binding(
            change_id, root=root
        )
    except ValueError as exc:
        raise S5FixtureReceiptError(
            "FROZEN_PLAN_BINDING_REQUIRED",
            "fixture receipt requires FROZEN plan_id and plan_fingerprint",
        ) from exc
    caller_plan_id = plan_id.strip()
    caller_fingerprint = plan_fingerprint.strip()
    if caller_plan_id and caller_plan_id != canonical_plan_id:
        raise S5FixtureReceiptError(
            "FROZEN_PLAN_BINDING_REFUSED",
            "caller plan_id does not match FROZEN plan on disk",
        )
    if caller_fingerprint:
        if len(caller_fingerprint) != 64 or caller_fingerprint != canonical_fingerprint:
            raise S5FixtureReceiptError(
                "FROZEN_PLAN_BINDING_REFUSED",
                "caller plan_fingerprint does not match FROZEN plan on disk",
            )
    plan_id = canonical_plan_id
    plan_fingerprint = canonical_fingerprint
    if status not in (FIXTURE_STATUS_PASS, FIXTURE_STATUS_FAIL, FIXTURE_STATUS_BLOCKED):
        raise S5FixtureReceiptError("RECEIPT_INVALID", f"invalid fixture STATUS: {status}")

    document: dict[str, Any] = {
        "schema_version": "bounded-verify-s5-fixture-receipt-v1",
        "obligation_id": obligation_id,
        "plan_id": plan_id,
        "plan_fingerprint": plan_fingerprint,
        "execution_id": execution_id,
        "input_digest": input_digest,
        "policy_identity": dict(policy_identity),
        "evidence_contract": evidence_contract,
        "evidence_producer": canonical_evidence_producer(obligation_id),
        "evidence_scope": FIXTURE_EVIDENCE_SCOPE,
        "production_evidence": False,
        "STATUS": status,
        "check_evidence": dict(check_evidence),
    }
    if is_production_s5_evidence(document):
        raise S5FixtureReceiptError(
            "RECEIPT_INVALID",
            "fixture receipt must not masquerade as production S5 evidence",
        )
    path = fixture_receipt_path(change_id, obligation_id, execution_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json_bytes(document)

    if path.is_file():
        existing = path.read_bytes()
        if existing == body:
            return FixtureReceiptRecord(change_id, obligation_id, execution_id, path)
        raise S5FixtureReceiptError(
            RECEIPT_IMMUTABLE_CONFLICT,
            "fixture receipt exists with different content for execution_id",
        )

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError as exc:
        raise S5FixtureReceiptError(
            RECEIPT_ALREADY_EXISTS,
            "concurrent fixture receipt publish",
        ) from exc
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    return FixtureReceiptRecord(change_id, obligation_id, execution_id, path)
