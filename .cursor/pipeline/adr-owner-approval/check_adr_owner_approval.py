# -*- coding: utf-8 -*-
"""Machine check for ADR Owner approval and archive authorization.

Проверяет evidence, привязанное к содержимому decision.md и к identity,
которую ``/opsx-verify`` уже сверяет у scenario report: ``CHANGE_ID`` и
``WORKTREE_FINGERPRINT`` (``HEAD=<git rev-parse HEAD>``).

Модуль не выпускает Owner signature и не переводит decision в Accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "adr-owner-approval-v1"
ARCHIVE_SCHEMA_VERSION = "adr-archive-authorization-v1"
CONTENT_HASH_ALGORITHM = "sha256-lf-normalized"
FINGERPRINT_RE = re.compile(r"^HEAD=[0-9a-f]{40}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE_RE = re.compile(r"^[A-Za-z0-9+/=_-]{32,}$")
PLACEHOLDER_SIGNATURES = frozenset(
    {
        "pending",
        "unsigned",
        "approved",
        "authorized",
        "yes",
        "true",
        "null",
        "none",
        "owner",
        "signature",
    }
)

APPROVED = "APPROVED"
AUTHORIZED = "AUTHORIZED"
PENDING = "PENDING"
MISSING = "MISSING"
STALE = "STALE"
INVALID = "INVALID"
PROMOTED_EARLY = "PROMOTED_EARLY"
APPROVAL_NOT_APPROVED = "APPROVAL_NOT_APPROVED"

EXIT_CODES = {
    APPROVED: 0,
    AUTHORIZED: 0,
    PENDING: 2,
    APPROVAL_NOT_APPROVED: 2,
    MISSING: 3,
    STALE: 4,
    INVALID: 5,
    PROMOTED_EARLY: 6,
}

APPROVAL_BOUND_FIELDS: tuple[str, ...] = (
    "adr_content_hash",
    "change_id",
    "content_hash_algorithm",
    "decision_id",
    "decision_path",
    "schema_version",
    "worktree_fingerprint",
)
ARCHIVE_BOUND_FIELDS: tuple[str, ...] = (
    "change_id",
    "decisions",
    "schema_version",
    "worktree_fingerprint",
)

# Нет функции, которая подставляет owner_signature. Константа для тестов и ревью.
SIGNATURE_MINTING = False


@dataclass(frozen=True)
class CheckResult:
    """Итог машинной проверки evidence. Не является Owner approval."""

    status: str
    detail: str
    adr_content_hash: str | None = None

    @property
    def exit_code(self) -> int:
        """Код процесса для CLI."""
        return EXIT_CODES[self.status]


def adr_content_hash(data: bytes) -> str:
    """sha256 LF-normalized байтов decision.md.

    Args:
        data: Сырые байты файла решения.

    Returns:
        64-символьный hex sha256.
    """
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Детерминированный JSON (UTF-8, sort_keys, без пробелов).

    Совпадает с ``bounded_verify_atomic.canonical_json_bytes``.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decision_status(text: str) -> str | None:
    """Возвращает первую непустую строку после ``## Status``."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "## Status":
            continue
        for follower in lines[index + 1 :]:
            stripped = follower.strip()
            if stripped:
                return stripped
        return None
    return None


def worktree_fingerprint(repo_root: Path) -> str:
    """``HEAD=<full sha>`` через ``git rev-parse HEAD``."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git rev-parse HEAD failed")
    return f"HEAD={completed.stdout.strip()}"


def _signature_ok(value: object) -> bool:
    if not isinstance(value, str):
        return False
    if value.strip().lower() in PLACEHOLDER_SIGNATURES:
        return False
    return SIGNATURE_RE.fullmatch(value) is not None


def _bound(document: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: document[field] for field in fields}


def _canonical_matches(document: Mapping[str, Any], fields: Sequence[str]) -> bool:
    signed = document.get("signed_canonical")
    if not isinstance(signed, str):
        return False
    return signed.encode("utf-8") == canonical_bytes(_bound(document, fields))


def render_pending_approval(
    *,
    change_id: str,
    decision_id: str,
    decision_path: str,
    decision_bytes: bytes,
    worktree_fingerprint_value: str,
) -> dict[str, Any]:
    """Собирает unsigned template. ``owner_signature`` всегда ``None``.

    Raises:
        ValueError: identity или hash не проходят формат.
    """
    if not FINGERPRINT_RE.fullmatch(worktree_fingerprint_value):
        raise ValueError("worktree_fingerprint must be HEAD=<40 lowercase hex>")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "change_id": change_id,
        "decision_id": decision_id,
        "decision_path": decision_path,
        "adr_content_hash": adr_content_hash(decision_bytes),
        "content_hash_algorithm": CONTENT_HASH_ALGORITHM,
        "worktree_fingerprint": worktree_fingerprint_value,
    }
    if not HASH_RE.fullmatch(payload["adr_content_hash"]):
        raise ValueError("adr_content_hash is not sha256 hex")
    document: dict[str, Any] = {
        **payload,
        "status": "pending",
        "owner_signature": None,
        "signed_canonical": canonical_bytes(payload).decode("utf-8"),
    }
    if document["owner_signature"] is not None:
        raise RuntimeError("pending template must not carry a signature")
    return document


def evaluate_approval(
    document: Mapping[str, Any] | None,
    *,
    decision_text: str | None,
    change_id: str,
    worktree_fingerprint_value: str,
) -> CheckResult:
    """Проверяет Owner approval evidence против текущего decision и identity.

    ``status=pending`` не является approval. Accepted внутри change-local
    файла не заменяет evidence.
    """
    if document is None:
        return CheckResult(MISSING, "owner approval evidence is absent")
    if decision_text is None:
        return CheckResult(MISSING, "decision file is absent")
    shape = _approval_shape_error(document)
    if shape is not None:
        return CheckResult(INVALID, shape)
    if not _canonical_matches(document, APPROVAL_BOUND_FIELDS):
        return CheckResult(INVALID, "signed_canonical does not match bound fields")
    status_line = decision_status(decision_text)
    content_hash = adr_content_hash(decision_text.encode("utf-8"))
    if status_line is None:
        return CheckResult(INVALID, "decision ## Status line is missing", content_hash)
    if status_line != "Proposed":
        return CheckResult(
            PROMOTED_EARLY,
            "change-local decision status is not Proposed",
            content_hash,
        )
    if document["change_id"] != change_id:
        return CheckResult(STALE, "change_id does not match", content_hash)
    if document["worktree_fingerprint"] != worktree_fingerprint_value:
        return CheckResult(STALE, "worktree_fingerprint does not match", content_hash)
    if document["adr_content_hash"] != content_hash:
        return CheckResult(STALE, "adr_content_hash does not match decision bytes", content_hash)
    if document["status"] == "pending":
        return CheckResult(PENDING, "owner approval is unsigned", content_hash)
    if document["status"] != "approved":
        return CheckResult(INVALID, "status is not pending or approved", content_hash)
    if not _signature_ok(document.get("owner_signature")):
        return CheckResult(INVALID, "owner_signature is missing or not Owner-shaped", content_hash)
    return CheckResult(APPROVED, "owner approval matches this decision and change identity", content_hash)


def _approval_shape_error(document: Mapping[str, Any]) -> str | None:
    required = set(APPROVAL_BOUND_FIELDS) | {"status", "owner_signature", "signed_canonical"}
    if set(document) != required:
        return "approval evidence fields do not match the schema"
    if document["schema_version"] != SCHEMA_VERSION:
        return "schema_version is not adr-owner-approval-v1"
    if document["content_hash_algorithm"] != CONTENT_HASH_ALGORITHM:
        return "content_hash_algorithm is not sha256-lf-normalized"
    if not isinstance(document["change_id"], str) or not document["change_id"]:
        return "change_id is empty"
    if not isinstance(document["decision_id"], str) or not document["decision_id"]:
        return "decision_id is empty"
    if not isinstance(document["decision_path"], str) or not document["decision_path"]:
        return "decision_path is empty"
    if not isinstance(document["adr_content_hash"], str) or not HASH_RE.fullmatch(
        document["adr_content_hash"]
    ):
        return "adr_content_hash is not 64 lowercase hex"
    if not isinstance(document["worktree_fingerprint"], str) or not FINGERPRINT_RE.fullmatch(
        document["worktree_fingerprint"]
    ):
        return "worktree_fingerprint is not HEAD=<40 lowercase hex>"
    if document["status"] not in {"pending", "approved"}:
        return "status is not pending or approved"
    signature = document["owner_signature"]
    if document["status"] == "pending" and signature is not None:
        return "pending evidence must keep owner_signature null"
    if document["status"] == "approved" and not isinstance(signature, str):
        return "approved evidence requires owner_signature"
    return None


def evaluate_archive_authorization(
    document: Mapping[str, Any] | None,
    *,
    change_id: str,
    worktree_fingerprint_value: str,
    approval_results: Mapping[str, CheckResult],
) -> CheckResult:
    """Проверяет отдельное archive authorization.

    Verify ``GO`` этим методом не подтверждается и не подменяется.
    """
    if document is None:
        return CheckResult(MISSING, "archive authorization evidence is absent")
    shape = _archive_shape_error(document)
    if shape is not None:
        return CheckResult(INVALID, shape)
    if not _canonical_matches(document, ARCHIVE_BOUND_FIELDS):
        return CheckResult(INVALID, "signed_canonical does not match bound fields")
    if document["change_id"] != change_id:
        return CheckResult(STALE, "change_id does not match")
    if document["worktree_fingerprint"] != worktree_fingerprint_value:
        return CheckResult(STALE, "worktree_fingerprint does not match")
    decisions = document["decisions"]
    if not isinstance(decisions, list) or not decisions:
        return CheckResult(INVALID, "decisions must be a non-empty list")
    for entry in decisions:
        if not isinstance(entry, dict):
            return CheckResult(INVALID, "decision entry is not an object")
        decision_id = entry.get("decision_id")
        expected_hash = entry.get("adr_content_hash")
        if not isinstance(decision_id, str):
            return CheckResult(INVALID, "decision_id is missing")
        result = approval_results.get(decision_id)
        if result is None:
            return CheckResult(APPROVAL_NOT_APPROVED, f"no approval check for {decision_id}")
        if result.status != APPROVED:
            return CheckResult(
                APPROVAL_NOT_APPROVED,
                f"approval for {decision_id} is {result.status}",
            )
        if result.adr_content_hash != expected_hash:
            return CheckResult(STALE, f"approval hash for {decision_id} does not match authorization")
    if document["status"] == "pending":
        return CheckResult(PENDING, "archive authorization is unsigned")
    if document["status"] != "authorized":
        return CheckResult(INVALID, "status is not pending or authorized")
    if not _signature_ok(document.get("owner_signature")):
        return CheckResult(INVALID, "owner_signature is missing or not Owner-shaped")
    return CheckResult(AUTHORIZED, "archive authorization matches this change identity")


def _archive_shape_error(document: Mapping[str, Any]) -> str | None:
    required = set(ARCHIVE_BOUND_FIELDS) | {"status", "owner_signature", "signed_canonical"}
    if set(document) != required:
        return "archive authorization fields do not match the schema"
    if document["schema_version"] != ARCHIVE_SCHEMA_VERSION:
        return "schema_version is not adr-archive-authorization-v1"
    if not isinstance(document["change_id"], str) or not document["change_id"]:
        return "change_id is empty"
    if not isinstance(document["worktree_fingerprint"], str) or not FINGERPRINT_RE.fullmatch(
        document["worktree_fingerprint"]
    ):
        return "worktree_fingerprint is not HEAD=<40 lowercase hex>"
    if document["status"] not in {"pending", "authorized"}:
        return "status is not pending or authorized"
    signature = document["owner_signature"]
    if document["status"] == "pending" and signature is not None:
        return "pending authorization must keep owner_signature null"
    if document["status"] == "authorized" and not isinstance(signature, str):
        return "authorized evidence requires owner_signature"
    decisions = document.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        return "decisions must be a non-empty list"
    for entry in decisions:
        if not isinstance(entry, dict):
            return "decision entry is not an object"
        needed = {"decision_id", "decision_path", "approval_path", "adr_content_hash"}
        if set(entry) != needed:
            return "decision entry fields do not match the schema"
        if not HASH_RE.fullmatch(str(entry["adr_content_hash"])):
            return "decision adr_content_hash is not 64 lowercase hex"
        for key in ("decision_id", "decision_path", "approval_path"):
            if not isinstance(entry[key], str) or not entry[key]:
                return f"{key} is empty"
    return None


def _read_json(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"__invalid_json__": True}
    if not isinstance(parsed, dict):
        return {"__invalid_json__": True}
    return parsed


def _print_result(kind: str, result: CheckResult) -> None:
    sys.stdout.write(f"{kind}: {result.status}\nDETAIL: {result.detail}\n")


def _approval_from_paths(
    *,
    evidence_path: Path,
    decision_path: Path,
    change_id: str,
    fingerprint: str,
) -> CheckResult:
    document = _read_json(evidence_path)
    if document is not None and document.get("__invalid_json__") is True:
        return CheckResult(INVALID, "approval evidence is not a JSON object")
    decision_text = decision_path.read_text(encoding="utf-8") if decision_path.is_file() else None
    return evaluate_approval(
        document,
        decision_text=decision_text,
        change_id=change_id,
        worktree_fingerprint_value=fingerprint,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``approval`` или ``archive-authorization``. Подпись не создаёт."""
    parser = argparse.ArgumentParser(description="Check ADR owner approval evidence")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    approval = sub.add_parser("approval")
    approval.add_argument("--change-id", required=True)
    approval.add_argument("--evidence", type=Path, required=True)
    approval.add_argument("--decision", type=Path, required=True)
    approval.add_argument("--fingerprint", default="")

    archive = sub.add_parser("archive-authorization")
    archive.add_argument("--change-id", required=True)
    archive.add_argument("--authorization", type=Path, required=True)
    archive.add_argument("--fingerprint", default="")

    args = parser.parse_args(list(argv) if argv is not None else None)
    fingerprint = args.fingerprint or worktree_fingerprint(args.repo_root)
    if args.command == "approval":
        result = _approval_from_paths(
            evidence_path=args.evidence,
            decision_path=args.decision,
            change_id=args.change_id,
            fingerprint=fingerprint,
        )
        _print_result("ADR_OWNER_APPROVAL_STATUS", result)
        return result.exit_code

    document = _read_json(args.authorization)
    if document is not None and document.get("__invalid_json__") is True:
        result = CheckResult(INVALID, "archive authorization is not a JSON object")
        _print_result("ADR_ARCHIVE_AUTHORIZATION_STATUS", result)
        return result.exit_code
    approval_results: dict[str, CheckResult] = {}
    if isinstance(document, Mapping) and isinstance(document.get("decisions"), list):
        for entry in document["decisions"]:
            if not isinstance(entry, dict):
                continue
            decision_id = entry.get("decision_id")
            decision_path = entry.get("decision_path")
            approval_path = entry.get("approval_path")
            if not all(isinstance(value, str) for value in (decision_id, decision_path, approval_path)):
                continue
            approval_results[str(decision_id)] = _approval_from_paths(
                evidence_path=args.repo_root / str(approval_path),
                decision_path=args.repo_root / str(decision_path),
                change_id=args.change_id,
                fingerprint=fingerprint,
            )
    result = evaluate_archive_authorization(
        document,
        change_id=args.change_id,
        worktree_fingerprint_value=fingerprint,
        approval_results=approval_results,
    )
    _print_result("ADR_ARCHIVE_AUTHORIZATION_STATUS", result)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
