# -*- coding: utf-8 -*-
"""Atomic assignment-level result binding (prevents concurrent writer races)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from typing import TYPE_CHECKING

from bounded_verify_atomic import canonical_json_bytes, read_canonical_json

if TYPE_CHECKING:
    from bounded_verify_result import ExpectedAssignment

REPO_ROOT = Path(__file__).resolve().parents[3]


def assignment_binding_key(assignment: "ExpectedAssignment") -> str:
    """Stable key for one ledger assignment identity."""
    payload = (
        f"{assignment.plan_fingerprint}|{assignment.ledger_revision}|"
        f"{assignment.check_id}|{assignment.assignment_epoch}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def binding_claim_path(change_id: str, binding_key: str, *, root: Path = REPO_ROOT) -> Path:
    from bounded_verify_result import results_dir

    return results_dir(change_id, root=root) / "bindings" / f"{binding_key}.json"


def acquire_assignment_binding(
    change_id: str,
    assignment: "ExpectedAssignment",
    execution_id: str,
    *,
    root: Path = REPO_ROOT,
) -> None:
    """O_CREAT|O_EXCL claim — at most one execution_id per assignment."""
    from bounded_verify_result import (
        BoundedVerifyResultError,
        RESULT_ASSIGNMENT_ALREADY_BOUND,
        results_dir,
    )

    key = assignment_binding_key(assignment)
    path = binding_claim_path(change_id, key, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "binding_key": key,
        "execution_id": execution_id,
        "plan_fingerprint": assignment.plan_fingerprint,
        "ledger_revision": assignment.ledger_revision,
        "assignment_epoch": assignment.assignment_epoch,
        "check_id": assignment.check_id,
    }
    body = canonical_json_bytes(document)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(path), flags, 0o644)
        try:
            os.write(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        existing = read_canonical_json(path)
        if (
            isinstance(existing, dict)
            and str(existing.get("execution_id", "")) == execution_id
        ):
            return
        raise BoundedVerifyResultError(
            RESULT_ASSIGNMENT_ALREADY_BOUND,
            "assignment binding already claimed by another execution_id",
        ) from None
