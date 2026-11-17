"""Идемпотентность APPLY_DESIGN: решение hop и служебный receipt.

Содержательный JSON не меняется при проверке receipt. Receipt пишется
отдельной атомарной записью только после PASS валидатора.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from workflows.artifact_paths import (
    apply_design_artifact_path,
    apply_design_hop_receipt_path,
)

_SCHEMA = "opsx.idempotency_receipt"
_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DesignSubmissionRecord:
    """Факт submit в этом хопе. Не доказывает валидность файла."""

    ok: bool
    unchanged: bool
    sha256: str | None
    code: str | None
    message: str | None = None


def remember_design_submission(
    accepted: DesignSubmissionRecord | None,
    latest: DesignSubmissionRecord,
) -> tuple[DesignSubmissionRecord | None, DesignSubmissionRecord]:
    """Не затирать принятую публикацию отклонённым candidate того же hop.

    ``INVALID_ARGS`` и прочие отказы до replace не меняют канон. Решение hop
    остаётся на последнем ``ok`` submit, а не на последнем вызове tool.
    """
    if latest.ok:
        return latest, latest
    if accepted is not None and accepted.ok:
        return accepted, accepted
    return None, latest


def design_hop_effective_status(answer_status: str, decision_status: str) -> str:
    """Принятый канон важнее ``ESCALATE_ARBITER``.

    ``artifact_sha256`` связывает review с байтами файла. Прошедший gate
    design идёт в review, а не в паузу из-за эскалации модели.
    """
    if decision_status in ("APPLY_COMPLETE", "NO_CHANGE"):
        return decision_status
    if answer_status == "ESCALATE_ARBITER":
        return "ESCALATE_ARBITER"
    return decision_status


@dataclass(frozen=True, slots=True)
class DesignHopDecision:
    """Чистое решение по submit, валидатору и свежести."""

    status: str
    code: str | None


def decide_design_hop(
    *,
    submission: DesignSubmissionRecord | None,
    artifact_present: bool,
    gate_ok: bool,
    snapshot_ok: bool,
) -> DesignHopDecision:
    """Выбрать статус hop без чтения диска.

    Успешный submit и PASS валидатора дают APPLY_COMPLETE (или NO_CHANGE,
    если байты не изменились). Без submit валидный свежий файл
    переиспользуется. Иначе BLOCKED. Завершение всегда требует ``snapshot_ok``.
    """
    if submission is not None and submission.ok and gate_ok and snapshot_ok:
        status = "NO_CHANGE" if submission.unchanged else "APPLY_COMPLETE"
        return DesignHopDecision(status=status, code=None)
    if submission is not None and submission.ok and gate_ok and not snapshot_ok:
        return DesignHopDecision(status="BLOCKED", code="STALE_APPLY_DESIGN")
    if submission is None and artifact_present and gate_ok and snapshot_ok:
        return DesignHopDecision(status="NO_CHANGE", code=None)
    if submission is not None and not submission.ok:
        return DesignHopDecision(
            status="BLOCKED",
            code=submission.code or "DESIGN_NOT_SUBMITTED",
        )
    if not artifact_present or submission is None:
        return DesignHopDecision(status="BLOCKED", code="DESIGN_NOT_SUBMITTED")
    return DesignHopDecision(status="BLOCKED", code="DESIGN_MACHINE_GATE_FAILED")


def design_hop_receipt_recovery_allowed(
    *,
    decision: DesignHopDecision,
    gate_ok: bool,
    snapshot_ok: bool,
) -> bool:
    """True только после принятия hop (APPLY_COMPLETE / NO_CHANGE), не при BLOCKED."""
    return (
        decision.status in ("APPLY_COMPLETE", "NO_CHANGE")
        and gate_ok
        and snapshot_ok
    )


def file_sha256(path: Path) -> str:
    """SHA-256 байтов файла."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints_from_design(design: Mapping[str, Any]) -> dict[str, str]:
    """Fingerprint входов из уже записанного design, без пересчёта по дереву."""
    planning = design.get("planning_identity")
    protected = design.get("protected_context_identity")
    planning_digest = ""
    protected_digest = ""
    if isinstance(planning, Mapping):
        planning_digest = str(planning.get("planning_digest") or "")
    if isinstance(protected, Mapping):
        protected_digest = str(protected.get("content_digest") or "")
    return {
        "planning_digest": planning_digest,
        "protected_context_digest": protected_digest,
    }


def receipt_matches(
    receipt: Mapping[str, Any],
    *,
    artifact_path: Path,
    repo_root: Path,
) -> bool:
    """True, если receipt указывает на тот же файл и тот же digest."""
    if receipt.get("schema") != _SCHEMA or receipt.get("schema_version") != _SCHEMA_VERSION:
        return False
    if str(receipt.get("status") or "") != "PASS":
        return False
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return False
    first = artifacts[0]
    if not isinstance(first, Mapping):
        return False
    rel = artifact_path.resolve().relative_to(repo_root.resolve()).as_posix()
    if str(first.get("path") or "") != rel:
        return False
    if not artifact_path.is_file():
        return False
    return str(first.get("sha256") or "") == file_sha256(artifact_path)


def build_receipt_payload(
    *,
    change_id: str,
    artifact_path: Path,
    repo_root: Path,
    design: Mapping[str, Any],
    attempt: int,
) -> dict[str, Any]:
    """Собрать receipt без копии design."""
    rel = artifact_path.resolve().relative_to(repo_root.resolve()).as_posix()
    return {
        "schema": _SCHEMA,
        "schema_version": _SCHEMA_VERSION,
        "node_id": "apply_design",
        "operation": "apply_design",
        "change_id": change_id,
        "slice_id": None,
        "status": "PASS",
        "input_fingerprints": fingerprints_from_design(design),
        "artifacts": [
            {
                "role": "apply_design",
                "path": rel,
                "sha256": file_sha256(artifact_path),
            }
        ],
        "validator": {"name": "opsx-apply-design.mjs", "version": "on-disk"},
        "attempt": attempt,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".hop-receipt-", suffix=".tmp")
    try:
        os.write(fd, text.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp_name, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def hop_receipt_present_and_valid(
    *,
    change_id: str,
    repo_root: Path,
) -> bool:
    """True, если receipt (C) существует и совпадает с каноническим APPLY-DESIGN."""
    dest = apply_design_hop_receipt_path(repo_root, change_id)
    if not dest.is_file():
        return False
    artifact = apply_design_artifact_path(repo_root, change_id)
    if not artifact.is_file():
        return False
    try:
        receipt = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    return receipt_matches(receipt, artifact_path=artifact, repo_root=repo_root)


async def maybe_recover_design_hop_receipt(
    *,
    decision: DesignHopDecision,
    change_id: str,
    repo_root: Path,
    gate_ok: bool,
    snapshot_ok: bool,
    attempt: int = 1,
) -> bool:
    """Восстановить receipt только после принятия hop; иначе no-op."""
    if not design_hop_receipt_recovery_allowed(
        decision=decision,
        gate_ok=gate_ok,
        snapshot_ok=snapshot_ok,
    ):
        return False
    return await recover_hop_receipt_if_missing(
        change_id=change_id,
        repo_root=repo_root,
        gate_ok=gate_ok,
        snapshot_ok=snapshot_ok,
        attempt=attempt,
    )


async def recover_hop_receipt_if_missing(
    *,
    change_id: str,
    repo_root: Path,
    gate_ok: bool,
    snapshot_ok: bool,
    attempt: int = 1,
) -> bool:
    """Восстановить receipt (C) без модели, если канон валиден и свежий."""
    if not gate_ok or not snapshot_ok:
        return False
    if await asyncio.to_thread(
        hop_receipt_present_and_valid,
        change_id=change_id,
        repo_root=repo_root,
    ):
        return True
    design = await asyncio.to_thread(load_design_bytes, change_id, repo_root=repo_root)
    if design is None:
        return False
    await write_hop_receipt(
        change_id=change_id,
        repo_root=repo_root,
        design=design,
        attempt=attempt,
    )
    return await asyncio.to_thread(
        hop_receipt_present_and_valid,
        change_id=change_id,
        repo_root=repo_root,
    )


async def write_hop_receipt(
    *,
    change_id: str,
    repo_root: Path,
    design: Mapping[str, Any],
    attempt: int = 1,
) -> Path:
    """Атомарно записать receipt. Канонический JSON не открывается на запись."""
    artifact = apply_design_artifact_path(repo_root, change_id)
    payload = await asyncio.to_thread(
        build_receipt_payload,
        change_id=change_id,
        artifact_path=artifact,
        repo_root=repo_root,
        design=design,
        attempt=attempt,
    )
    dest = apply_design_hop_receipt_path(repo_root, change_id)
    await asyncio.to_thread(_atomic_write_json, dest, payload)
    return dest


def load_design_bytes(change_id: str, *, repo_root: Path) -> dict[str, Any] | None:
    """Прочитать канонический APPLY-DESIGN, если он есть."""
    path = apply_design_artifact_path(repo_root, change_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None
