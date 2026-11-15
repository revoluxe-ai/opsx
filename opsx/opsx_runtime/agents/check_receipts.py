"""Small, framework-independent receipts for expensive read-only checks.

A PASS can be reused only with the same check contract and current content
fingerprint. Calling code owns the input selection and verdict validation.
No LangGraph state, model retries, agent sessions or inferred PASS here.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from agents.utils.receipt_file_identity import content_digest_matches_stored
from workflows.artifact_paths import receipts_dir

_LOG = logging.getLogger(__name__)
_VERSION = 1
SCENARIO_COVERAGE_CONTRACT = "scenario_coverage_audit_v1"
_MAX_RECEIPT = 2 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_CHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def content_digest(root: Path, paths: Iterable[Path]) -> str:
    """Hash ONLY caller-declared files, including dirty/untracked content.

    Directories must be expanded by the caller, allowing it to define which
    paths are relevant. Missing or unsafe inputs fail closed rather than
    inventing a cache HIT. This is not a whole-repo git HEAD hash.
    """
    base = root.resolve()
    selected = sorted(set(paths), key=lambda p: str(p))
    if not selected:
        raise ValueError("CHECK_INPUTS_EMPTY")
    if len(selected) > 10000:
        raise ValueError("CHECK_INPUTS_TOO_MANY")
    digest = hashlib.sha256(b"opsx-check-inputs-v1\0")
    size = 0
    for path in selected:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"CHECK_INPUT_MISSING_OR_LINK: {path}")
        full = path.resolve()
        if not full.is_relative_to(base):
            raise ValueError(f"CHECK_INPUT_OUT_OF_SCOPE: {path}")
        rel = full.relative_to(base).as_posix()
        digest.update(rel.encode("utf-8") + b"\0")
        with full.open("rb") as stream:
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > 200 * 1024 * 1024:
                    raise ValueError("CHECK_INPUTS_TOO_LARGE")
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _product_digest_ok(
    item: Mapping[str, Any],
    input_digest: str,
    root: Path,
    input_paths: Iterable[Path] | None,
) -> bool:
    bound = item.get("product_input_digest")
    if not isinstance(bound, str):
        return False
    if bound == input_digest:
        return True
    if input_paths is None:
        return False
    return input_digest_matches(bound, root, input_paths, current_hex=input_digest)


def input_digest_matches(
    stored_hex: str,
    root: Path,
    paths: Iterable[Path],
    *,
    current_hex: str | None = None,
) -> bool:
    """True, если сохранённый input digest совпадает с файлами (сырой или нормализованный текст)."""
    if current_hex is not None and stored_hex == current_hex:
        return True
    base = root.resolve()
    rel_data: list[tuple[str, bytes]] = []
    for path in sorted(set(paths), key=lambda p: str(p)):
        if path.is_symlink() or not path.is_file():
            continue
        full = path.resolve()
        if not full.is_relative_to(base):
            continue
        rel_data.append((full.relative_to(base).as_posix(), full.read_bytes()))
    if not rel_data:
        return False
    return content_digest_matches_stored(stored_hex, rel_data)


def _receipt_path(root: Path, change_id: str, check: str) -> Path:
    if not _SAFE_CHANGE.fullmatch(change_id) or change_id in (".", ".."):
        raise ValueError("CHECK_CHANGE_INVALID")
    if not _SAFE_NAME.fullmatch(check):
        raise ValueError("CHECK_NAME_INVALID")
    repo = root.resolve()
    change = resource_path(repo, "openspec", "changes", change_id)
    if not change.is_dir() or change.is_symlink() or change.resolve() != change:
        raise ValueError("CHECK_CHANGE_UNSAFE")
    directory = receipts_dir(repo, change_id) / "check-receipts"
    directory = directory.resolve()
    if directory.is_symlink() or not directory.resolve().is_relative_to(repo):
        raise ValueError("CHECK_RECEIPT_DIR_UNSAFE")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{check}.json"


def _result_verdict(result: Mapping[str, Any]) -> Any:
    return result.get("verdict", result.get("status", result.get("quality_gate_verdict", result.get("coverage_activation_verdict"))))


def read_pass(
    root: Path,
    *,
    change_id: str,
    check: str,
    contract: str,
    input_digest: str,
    pass_verdict: str,
    verifier_contract_digest: str | None = None,
    ignore_verifier_contract: bool = False,
    input_paths: Iterable[Path] | None = None,
) -> dict[str, Any] | None:
    """Only return a structurally valid, exact-input PASS candidate.

    The caller MUST validate the result against its own schema and must not
    treat the stored origin run as a run performed in the current execution.
    ``ignore_verifier_contract`` reads legacy implementation-review receipts
    after reviewer identity was removed; all input/checksum/verdict checks stay.
    """
    try:
        path = _receipt_path(root, change_id, check)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_RECEIPT:
            return None
        raw = path.read_bytes()
        if len(raw) > _MAX_RECEIPT:
            return None
        item = json.loads(raw)
        if not isinstance(item, dict):
            return None
        checksum = item.get("checksum")
        body = {key: value for key, value in item.items() if key != "checksum"}
        if not isinstance(checksum, str) or checksum != _hash(body):
            return None
        bound_digest = item.get("input_digest")
        digest_ok = isinstance(bound_digest, str) and bound_digest == input_digest
        if not digest_ok and isinstance(bound_digest, str) and input_paths is not None:
            digest_ok = input_digest_matches(
                bound_digest, root, input_paths, current_hex=input_digest,
            )
        if (
            item.get("version") != _VERSION
            or item.get("check") != check
            or item.get("change_id") != change_id
            or item.get("contract") != contract
            or not digest_ok
            or (not ignore_verifier_contract and (
                verifier_contract_digest is not None and (
                    not _product_digest_ok(item, input_digest, root, input_paths)
                    or item.get("verifier_contract_digest") != verifier_contract_digest
                )
            ))
            or (not ignore_verifier_contract and (
                verifier_contract_digest is None
                and item.get("verifier_contract_digest") is not None
            ))
            or (not ignore_verifier_contract and (
                contract == "read_only_review_v2"
                and verifier_contract_digest is None
            ))
            or item.get("status") != "PASS"
            or item.get("pass_verdict") != pass_verdict
            or not isinstance(item.get("origin_execution_id"), str)
            or not item["origin_execution_id"].strip()
            or not isinstance(item.get("evidence_id"), str)
            or not item["evidence_id"].strip()
            or not isinstance(item.get("result"), dict)
            or _result_verdict(item["result"]) != pass_verdict
        ):
            return None
        return item
    except (OSError, ValueError, TypeError):
        return None


def write_pass(
    root: Path,
    *,
    change_id: str,
    check: str,
    contract: str,
    input_digest: str,
    origin_execution_id: str,
    evidence_id: str,
    pass_verdict: str,
    result: Mapping[str, Any],
    verifier_contract_digest: str | None = None,
) -> None:
    """Atomic single-file PASS publication. FAIL/UNKNOWN are never cached.

    Write failures should be logged by the caller; they must not convert a
    genuinely verified result to FAIL or manufacture a PASS.
    """
    if not all(
        isinstance(x, str) and x.strip()
        for x in (contract, input_digest, origin_execution_id, evidence_id)
    ):
        raise ValueError("CHECK_RECEIPT_UNBOUND")
    if not isinstance(pass_verdict, str) or not pass_verdict.strip() or _result_verdict(result) != pass_verdict:
        raise ValueError("CHECK_RECEIPT_VERDICT_MISMATCH")
    if (contract == "read_only_review_v2" and verifier_contract_digest is None) or (
        verifier_contract_digest is not None and (
            not isinstance(verifier_contract_digest, str) or not verifier_contract_digest.strip()
        )
    ):
        raise ValueError("CHECK_VERIFIER_CONTRACT_UNBOUND")
    path = _receipt_path(root, change_id, check)
    if path.is_symlink():
        raise ValueError("CHECK_RECEIPT_SYMLINK")
    body = {
        "version": _VERSION,
        "check": check,
        "change_id": change_id,
        "contract": contract,
        "input_digest": input_digest,
        "status": "PASS",
        "pass_verdict": pass_verdict,
        "origin_execution_id": origin_execution_id,
        "evidence_id": evidence_id,
        "result": dict(result),
    }
    if verifier_contract_digest is not None:
        body["product_input_digest"] = input_digest
        body["verifier_contract_digest"] = verifier_contract_digest
    encoded = _json({**body, "checksum": _hash(body)}) + b"\n"
    if len(encoded) > _MAX_RECEIPT:
        raise ValueError("CHECK_RECEIPT_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as fp:
            name = fp.name
            fp.write(encoded)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def best_effort_write_pass(**kwargs: Any) -> bool:
    """Receipt failure is observable but never a failure of the passed check."""
    try:
        write_pass(**kwargs)
    except (ValueError, TypeError, OSError) as exc:
        _LOG.warning("check_receipt_write_failed check=%s reason=%s", kwargs.get("check"), type(exc).__name__)
        return False
    return True

