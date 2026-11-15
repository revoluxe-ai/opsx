# -*- coding: utf-8 -*-
"""Immutable result evidence для bounded verification (R-V1-005B/C).

``create_result`` — exclusive O_CREAT writer (Protocol B); drain validation —
для ``bounded_verify_ledger.schedule_write``. Result PASS ≠ aggregate PASS.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

from bounded_verify_assignment_binding import acquire_assignment_binding
from bounded_verify_atomic import atomic_publish_json, canonical_json_bytes, read_canonical_json

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULT_SCHEMA_PATH = (
    REPO_ROOT
    / ".cursor"
    / "pipeline"
    / "contracts"
    / "bounded-verify"
    / "RESULT_SCHEMA.json"
)

RESULT_SCHEMA_VERSION = "bounded-verify-result-v1"

RESULT_STALE_PLAN = "RESULT_STALE_PLAN"
RESULT_STALE_LEDGER = "RESULT_STALE_LEDGER"
RESULT_STALE_ASSIGNMENT = "RESULT_STALE_ASSIGNMENT"
RESULT_STALE_SNAPSHOT = "RESULT_STALE_SNAPSHOT"
RESULT_CHECK_MISMATCH = "RESULT_CHECK_MISMATCH"
RESULT_INCOMPLETE = "RESULT_INCOMPLETE"
RESULT_ID_MISMATCH = "RESULT_ID_MISMATCH"
RESULT_INVALID = "RESULT_INVALID"
RESULT_ALREADY_EXISTS = "RESULT_ALREADY_EXISTS"
RESULT_ASSIGNMENT_ALREADY_BOUND = "RESULT_ASSIGNMENT_ALREADY_BOUND"
RESULT_AMBIGUOUS_ASSIGNMENT = "RESULT_AMBIGUOUS_ASSIGNMENT"


class BoundedVerifyResultError(Exception):
    """Ошибка валидации result для drain."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DrainDecision:
    """Решение drain: можно ли продвинуть ledger по result."""

    allowed: bool
    terminal_state: Optional[str] = None
    code: str = "OK"
    message: str = ""


@dataclass(frozen=True)
class ExpectedAssignment:
    """Ожидаемое CURRENT assignment при drain."""

    check_id: str
    assignment_epoch: int
    ledger_revision: int
    plan_fingerprint: str
    content_digest: str | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    """Исход выполнения check для create_result (immutable evidence)."""

    execution_id: str
    result_status: str
    worker_packet_or_equivalent: Optional[Mapping[str, Any]] = None
    registry_check_id_snapshot: Optional[str] = None


@dataclass(frozen=True)
class ResultRecord:
    """Опубликованный immutable result artifact."""

    change_id: str
    result_id: str
    plan_fingerprint: str
    ledger_revision: int
    assignment_epoch: int
    check_id: str
    execution_id: str
    result_status: str
    carrier_path: Path


def results_dir(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Каталог results для change."""
    return (
        root / "openspec" / "changes" / change_id / "bounded-verify" / "results"
    )


def compute_result_id(
    plan_fingerprint: str,
    ledger_revision: int,
    check_id: str,
    execution_id: str,
) -> str:
    """sha256(plan_fingerprint|ledger_revision|check_id|execution_id)."""
    payload = f"{plan_fingerprint}|{ledger_revision}|{check_id}|{execution_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def result_carrier_path(
    change_id: str,
    result_id: str,
    *,
    root: Path = REPO_ROOT,
) -> Path:
    """Путь к immutable result artifact."""
    return results_dir(change_id, root=root) / f"{result_id}.json"


def _validate_result_document(doc: Mapping[str, Any]) -> None:
    """Минимальная schema validation по RESULT_SCHEMA.json."""
    required = (
        "schema_version",
        "result_id",
        "plan_fingerprint",
        "ledger_revision",
        "assignment_epoch",
        "check_id",
        "execution_id",
        "result_status",
        "producer",
    )
    for key in required:
        if key not in doc:
            raise BoundedVerifyResultError(RESULT_INVALID, f"missing field: {key}")

    if doc["schema_version"] != RESULT_SCHEMA_VERSION:
        raise BoundedVerifyResultError(RESULT_INVALID, "invalid schema_version")

    status = str(doc["result_status"])
    if status not in ("PASS", "FAIL", "EXECUTION_ERROR", "INCOMPLETE"):
        raise BoundedVerifyResultError(RESULT_INVALID, "invalid result_status")

    fingerprint = str(doc["plan_fingerprint"])
    if len(fingerprint) != 64:
        raise BoundedVerifyResultError(RESULT_INVALID, "invalid plan_fingerprint")

    expected_id = compute_result_id(
        fingerprint,
        int(doc["ledger_revision"]),
        str(doc["check_id"]),
        str(doc["execution_id"]),
    )
    if str(doc["result_id"]) != expected_id:
        raise BoundedVerifyResultError(RESULT_ID_MISMATCH, "result_id mismatch")


def read_result(
    change_id: str,
    result_id: str,
    *,
    root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Читает canonical result; отказывает если absent/invalid."""
    path = result_carrier_path(change_id, result_id, root=root)
    doc = read_canonical_json(path)
    if doc is None:
        raise BoundedVerifyResultError(RESULT_INVALID, "result not found or invalid")
    try:
        _validate_result_document(doc)
    except BoundedVerifyResultError:
        raise
    return dict(doc)


def _matches_assignment(doc: Mapping[str, Any], assignment: ExpectedAssignment) -> bool:
    return (
        doc.get("check_id") == assignment.check_id
        and int(doc.get("assignment_epoch", -1)) == assignment.assignment_epoch
        and int(doc.get("ledger_revision", -1)) == assignment.ledger_revision
        and str(doc.get("plan_fingerprint", "")) == assignment.plan_fingerprint
    )


def find_results_for_assignment(
    change_id: str,
    assignment: ExpectedAssignment,
    *,
    root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], ...]:
    """Все canonical results, привязанные к assignment identity."""
    rdir = results_dir(change_id, root=root)
    if not rdir.is_dir():
        return ()
    matches: list[dict[str, Any]] = []
    for path in sorted(rdir.glob("*.json")):
        doc = read_canonical_json(path)
        if doc is None:
            continue
        try:
            _validate_result_document(doc)
        except BoundedVerifyResultError:
            continue
        if _matches_assignment(doc, assignment):
            matches.append(dict(doc))
    return tuple(matches)


def _record_assignment_conflict(
    change_id: str,
    assignment: ExpectedAssignment,
    matches: tuple[dict[str, Any], ...],
    *,
    root: Path,
) -> None:
    """Append-only auditable conflict evidence (does not mutate results)."""
    from bounded_verify_assignment_binding import assignment_binding_key

    key = assignment_binding_key(assignment)
    path = results_dir(change_id, root=root) / "conflicts" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "binding_key": key,
        "assignment": {
            "check_id": assignment.check_id,
            "assignment_epoch": assignment.assignment_epoch,
            "ledger_revision": assignment.ledger_revision,
            "plan_fingerprint": assignment.plan_fingerprint,
        },
        "result_ids": [str(m.get("result_id", "")) for m in matches],
        "statuses": [str(m.get("result_status", "")) for m in matches],
        "recovery_required": True,
    }
    if not path.is_file():
        atomic_publish_json(path, payload, validate=lambda _: None)


def find_result_for_current(
    change_id: str,
    assignment: ExpectedAssignment,
    *,
    root: Path = REPO_ROOT,
) -> Optional[dict[str, Any]]:
    """Ищет authoritative result для CURRENT assignment (FAIL wins on conflict)."""
    matches = find_results_for_assignment(change_id, assignment, root=root)
    if not matches:
        return None
    fails = [m for m in matches if str(m.get("result_status")) == "FAIL"]
    passes = [m for m in matches if str(m.get("result_status")) == "PASS"]
    if len(fails) == 1 and not passes:
        return fails[0]
    if len(passes) == 1 and not fails:
        return passes[0]
    if fails and passes:
        return fails[0]
    if len(fails) > 1 or len(passes) > 1:
        _record_assignment_conflict(change_id, assignment, matches, root=root)
        raise BoundedVerifyResultError(
            RESULT_AMBIGUOUS_ASSIGNMENT,
            "ambiguous results for assignment; explicit recovery required",
        )
    return matches[0]


def validate_result_for_drain(
    result: Mapping[str, Any],
    expected: ExpectedAssignment,
) -> DrainDecision:
    """Проверяет result как authoritative input к ledger transition (T06).

    Только PASS/FAIL разрешают drain. Presence без valid schema — insufficient.
    """
    try:
        _validate_result_document(result)
    except BoundedVerifyResultError as exc:
        return DrainDecision(
            allowed=False,
            code=RESULT_INCOMPLETE,
            message=exc.message,
        )

    if result["plan_fingerprint"] != expected.plan_fingerprint:
        return DrainDecision(
            allowed=False,
            code=RESULT_STALE_PLAN,
            message="plan_fingerprint mismatch",
        )
    if int(result["ledger_revision"]) != expected.ledger_revision:
        return DrainDecision(
            allowed=False,
            code=RESULT_STALE_LEDGER,
            message="ledger_revision mismatch",
        )
    if int(result["assignment_epoch"]) != expected.assignment_epoch:
        return DrainDecision(
            allowed=False,
            code=RESULT_STALE_ASSIGNMENT,
            message="assignment_epoch mismatch",
        )
    if result["check_id"] != expected.check_id:
        return DrainDecision(
            allowed=False,
            code=RESULT_CHECK_MISMATCH,
            message="check_id mismatch",
        )

    if expected.content_digest is not None:
        echo = str(result.get("content_digest", ""))
        if echo != expected.content_digest:
            return DrainDecision(
                allowed=False,
                code=RESULT_STALE_SNAPSHOT,
                message="content_digest mismatch",
            )

    status = str(result["result_status"])
    if status in ("EXECUTION_ERROR", "INCOMPLETE"):
        return DrainDecision(
            allowed=False,
            code=RESULT_INCOMPLETE,
            message=f"non-drainable status: {status}",
        )
    if status not in ("PASS", "FAIL"):
        return DrainDecision(
            allowed=False,
            code=RESULT_INCOMPLETE,
            message=f"unknown status: {status}",
        )

    return DrainDecision(allowed=True, terminal_state=status, code="OK")


def load_result_schema() -> Mapping[str, Any]:
    """Загружает RESULT_SCHEMA.json (для тестов/диагностики)."""
    return json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))


def create_result(
    change_id: str,
    assignment: ExpectedAssignment,
    outcome: ExecutionOutcome,
    *,
    root: Path = REPO_ROOT,
    producer: str = "bounded_verify_executor",
) -> ResultRecord:
    """Exclusive-create immutable result (Protocol B — O_CREAT|O_EXCL).

    Partial/corrupt files не считаются canonical; drain не продвигает ledger.
    Повтор с тем же ``execution_id`` → ``RESULT_ALREADY_EXISTS``.

    Args:
        change_id: OpenSpec change id.
        assignment: Точная CURRENT identity на момент записи.
        outcome: execution_id + result_status + optional worker packet.
        root: Repo root для hermetic tests.
        producer: Модуль-писатель evidence.

    Returns:
        ResultRecord с путём к опубликованному artifact.

    Raises:
        BoundedVerifyResultError: schema/identity/collision.
    """
    if outcome.result_status not in ("PASS", "FAIL", "EXECUTION_ERROR", "INCOMPLETE"):
        raise BoundedVerifyResultError(
            RESULT_INVALID,
            f"invalid result_status: {outcome.result_status}",
        )

    acquire_assignment_binding(change_id, assignment, outcome.execution_id, root=root)

    result_id = compute_result_id(
        assignment.plan_fingerprint,
        assignment.ledger_revision,
        assignment.check_id,
        outcome.execution_id,
    )

    document: dict[str, Any] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "plan_fingerprint": assignment.plan_fingerprint,
        "ledger_revision": assignment.ledger_revision,
        "assignment_epoch": assignment.assignment_epoch,
        "check_id": assignment.check_id,
        "execution_id": outcome.execution_id,
        "result_status": outcome.result_status,
        "producer": producer,
    }
    if outcome.registry_check_id_snapshot is not None:
        document["registry_check_id_snapshot"] = outcome.registry_check_id_snapshot
    if outcome.worker_packet_or_equivalent is not None:
        document["worker_packet_or_equivalent"] = dict(
            outcome.worker_packet_or_equivalent
        )
    if assignment.content_digest is not None:
        document["content_digest"] = assignment.content_digest

    _validate_result_document(document)

    path = result_carrier_path(change_id, result_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_json_bytes(document)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError as exc:
        raise BoundedVerifyResultError(
            RESULT_ALREADY_EXISTS,
            f"result already exists: {result_id}",
        ) from exc

    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)

    return ResultRecord(
        change_id=change_id,
        result_id=result_id,
        plan_fingerprint=assignment.plan_fingerprint,
        ledger_revision=assignment.ledger_revision,
        assignment_epoch=assignment.assignment_epoch,
        check_id=assignment.check_id,
        execution_id=outcome.execution_id,
        result_status=outcome.result_status,
        carrier_path=path,
    )
