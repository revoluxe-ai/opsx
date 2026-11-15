"""Slice apply-stage gate через ``CheckWorkflowReceipt`` (scope + content_digest).

На диске в machine receipt поле ``work_scope_digest`` (тот же алгоритм, что verify routing).
PRE-hop: hydrate ``cycle_checks.established`` из JSON → ``check()`` → ``MATCH`` ⇒ SKIP.
Миграция: закрытый receipt без digest → backfill с диска без агента.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from check_workflow_receipt import CheckWorkflowReceipt, CheckWorkflowReceiptStatus
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt import (
    _SUCCESS_STATUSES,
    green_work_paths,
    load_stage_receipt,
    normalize_work_path,
    receipt_json_path,
    stage_scope_overlay_path,
    work_paths_from_receipt,
)
from workflows.artifact_paths import useful_stage_receipt_path
from workflows.implementation.red_operative_validation import quarantine_inoperative_red_scope
from workflows.implementation.slice_stage_contract import (
    repair_blocks_receipt_reuse,
    stage_receipt_contract_accepted,
)
from workflows.verify.cycle_checks import CYCLE_CHECKS_STATE_KEY

WORK_SCOPE_DIGEST_KEY = "work_scope_digest"

_TRANSIENT_RECEIPT_ERRNOS = frozenset({13, 22, 32})
_RECEIPT_WRITE_ATTEMPTS = 3
_RECEIPT_WRITE_RETRY_DELAY_S = 0.05


def _atomic_write_utf8_lf(path: Path, text: str) -> None:
    """Атомарно записать UTF-8 с LF (temp + replace); retry на transient OSError."""
    data = (text + "\n").encode("utf-8")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    last_exc: OSError | None = None
    for attempt in range(_RECEIPT_WRITE_ATTEMPTS):
        tmp = parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            _ = tmp.write_bytes(data)
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_exc = exc
            if tmp.is_file():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            errno = getattr(exc, "errno", None)
            if errno not in _TRANSIENT_RECEIPT_ERRNOS or attempt + 1 >= _RECEIPT_WRITE_ATTEMPTS:
                raise
            time.sleep(_RECEIPT_WRITE_RETRY_DELAY_S)
    if last_exc is not None:
        raise last_exc


@dataclass(frozen=True, slots=True)
class SliceEstablishedKey:
    """Ключ ``cycle_checks.established`` для slice/stage (не расширяем verify ``CheckName``)."""

    value: str


@dataclass(frozen=True, slots=True)
class SliceWorkflowReceiptEvaluation:
    """Результат PRE/POST gate для hop и dispatch JSON."""

    valid: bool
    code: str | None
    work_paths: tuple[str, ...]
    backfilled_digest: bool = False
    receipt_status: CheckWorkflowReceiptStatus | None = None


def established_key(change_id: str, slice_id: str, stage: str) -> str:
    """Стабильный ключ established для hydrate/check."""
    return f"impl_slice:{change_id}:{slice_id}:{stage}"


def build_slice_check_receipt(
    key: SliceEstablishedKey,
    work_paths: tuple[str, ...],
) -> CheckWorkflowReceipt:
    """``CheckWorkflowReceipt`` с маской = минимальные work paths стадии."""
    normalized = tuple(
        p for p in (normalize_work_path(x) for x in work_paths) if p
    )
    return CheckWorkflowReceipt(
        key,  # type: ignore[arg-type]
        normalized,
        include_change_artifacts=False,
        exclude_tests_opsx=False,
    )


def hydrate_state_for_check(
    state: Mapping[str, Any],
    key: str,
    stored_digest: str | None,
) -> dict[str, Any]:
    """Подмешать сохранённый digest в ``cycle_checks`` для ``check()``."""
    merged = dict(state)
    established: dict[str, str] = {}
    if stored_digest and stored_digest.strip():
        established[key] = stored_digest.strip()
    merged[CYCLE_CHECKS_STATE_KEY] = {"established": established}
    return merged


def _state_for_digest(repo_root: Path, change_id: str) -> dict[str, Any]:
    return {
        "repository_path": str(repo_root.resolve()),
        "change_id": change_id,
    }


def _all_work_files_present(repo_root: Path, paths: tuple[str, ...]) -> bool:
    root = repo_root.resolve()
    for rel in paths:
        normalized = normalize_work_path(rel)
        if not normalized:
            return False
        candidate = (resource_path(root, normalized)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return False
        if not candidate.is_file():
            return False
    return True


def compute_work_scope_digest(
    receipt: CheckWorkflowReceipt,
    *,
    repo_root: Path,
    change_id: str,
) -> str | None:
    """Текущий digest scope (``None`` если файлов нет или digest недоступен)."""
    state = _state_for_digest(repo_root, change_id)
    return receipt._digest(state)


def persist_work_scope_digest(
    change_id: str,
    slice_id: str,
    stage: str,
    digest: str,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Записать ``work_scope_digest`` в service overlay (не дублировать useful JSON)."""
    machine = load_stage_receipt(change_id, slice_id, stage, repo_root=repo_root)
    if machine is None:
        raise FileNotFoundError(f"receipt missing for {change_id}/{slice_id}/{stage}")
    overlay_path = stage_scope_overlay_path(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
    )
    overlay: dict[str, Any] = {
        "schema": "opsx.slice_scope_overlay",
        "schema_version": 1,
        "change_id": change_id,
        "slice_id": slice_id,
        "stage": stage,
        WORK_SCOPE_DIGEST_KEY: digest.strip(),
        "useful_machine_path": receipt_json_path(
            change_id,
            slice_id,
            stage,
            repo_root=repo_root,
        ).relative_to(repo_root.resolve()).as_posix(),
    }
    text = json.dumps(overlay, indent=2, ensure_ascii=False, sort_keys=True)
    _atomic_write_utf8_lf(overlay_path, text)
    merged = dict(machine)
    merged[WORK_SCOPE_DIGEST_KEY] = digest.strip()
    return merged


def _resolve_work_paths(
    change_id: str,
    slice_id: str,
    stage: str,
    receipt: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[str, ...] | None:
    """Вернуть нормализованные work paths или ``None`` если статус-only stub."""
    if stage == STAGE_APPLY_GREEN:
        paths = tuple(
            normalize_work_path(p)
            for p in green_work_paths(change_id, slice_id, repo_root=repo_root)
            if normalize_work_path(p)
        )
    else:
        paths = tuple(
            normalize_work_path(p)
            for p in work_paths_from_receipt(stage, receipt)
            if normalize_work_path(p)
        )
    if stage == STAGE_APPLY_SCAFFOLD and not paths:
        return ()
    if not paths and stage == STAGE_APPLY_IMPLEMENT:
        if str(receipt.get("status") or "") == "IMPLEMENTATION_ADOPTED":
            return ()
        return None
    if not paths and stage != STAGE_APPLY_SCAFFOLD:
        return None
    return tuple(sorted(set(paths)))


def try_backfill_work_scope_digest(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
    work_paths: tuple[str, ...],
    check_receipt: CheckWorkflowReceipt,
) -> tuple[str | None, bool]:
    """Миграция: посчитать digest с диска и записать, если файлы на месте.

    Returns:
        ``(digest, wrote)`` — digest после backfill; ``wrote=True`` если JSON обновлён.
    """
    if work_paths and not _all_work_files_present(repo_root, work_paths):
        return None, False
    digest = compute_work_scope_digest(
        check_receipt,
        repo_root=repo_root,
        change_id=change_id,
    )
    if work_paths and not digest:
        return None, False
    if not work_paths:
        return None, False
    machine = load_stage_receipt(change_id, slice_id, stage, repo_root=repo_root)
    if machine is None:
        return None, False
    stored = str(machine.get(WORK_SCOPE_DIGEST_KEY) or "").strip()
    if stored == digest:
        return digest, False
    _ = persist_work_scope_digest(
        change_id,
        slice_id,
        stage,
        digest,
        repo_root=repo_root,
    )
    return digest, True


def project_scaffold_receipt_for_operative_gate(
    receipt: Mapping[str, Any],
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Собрать receipt так, как его увидит operative gate после POST ``work_scope_digest``.

    Runtime может дописать ``work_scope_digest`` сразу после hop (``persist_after_successful_hop``).
    Edit-guard и post-hop machine validator должны проверять эту проекцию, а не только тело модели.
    """
    data = dict(receipt)
    change_id = str(data.get("change_id") or "").strip()
    slice_id = str(data.get("slice_id") or "").strip()
    if not change_id or not slice_id:
        return data
    paths = _resolve_work_paths(
        change_id,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        data,
        repo_root=repo_root,
    )
    if not paths:
        return data
    if not _all_work_files_present(repo_root, paths):
        return data
    key = established_key(change_id, slice_id, STAGE_APPLY_SCAFFOLD)
    check_receipt = build_slice_check_receipt(SliceEstablishedKey(key), paths)
    digest = compute_work_scope_digest(
        check_receipt,
        repo_root=repo_root,
        change_id=change_id,
    )
    if digest:
        data[WORK_SCOPE_DIGEST_KEY] = digest
    return data


def evaluate_slice_stage(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
    state: Mapping[str, Any] | None = None,
    block_active_repair_reuse: bool = True,
) -> SliceWorkflowReceiptEvaluation:
    """PRE gate: статус receipt + ``CheckWorkflowReceipt.check()`` (+ миграция digest).

    Useful JSON в ``openspec/changes`` обязателен для apply-stage gate: legacy
    ``opsx/receipts`` не считается принятым телом (implement materialize читает openspec).

    Args:
        block_active_repair_reuse: при ``MATCH`` и активном repair запретить reuse (PRE/skip).
            POST после ``persist_after_successful_hop`` передаёт ``False``, чтобы не
            падать на только что записанном digest.
    """
    useful_path = useful_stage_receipt_path(repo_root, change_id, stage, slice_id)
    if stage in (
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    ) and not useful_path.is_file():
        return SliceWorkflowReceiptEvaluation(False, "RECEIPT_MISSING", ())
    receipt = load_stage_receipt(change_id, slice_id, stage, repo_root=repo_root)
    if receipt is None:
        return SliceWorkflowReceiptEvaluation(False, "RECEIPT_MISSING", ())
    status = str(receipt.get("status") or "")
    ok_statuses = _SUCCESS_STATUSES.get(stage, frozenset())
    if status not in ok_statuses:
        return SliceWorkflowReceiptEvaluation(False, "BAD_STATUS", ())

    if (
        stage == STAGE_APPLY_RED
        and status in ok_statuses
        and not stage_receipt_contract_accepted(
            change_id,
            slice_id,
            stage,
            receipt,
            repo_root=repo_root,
        )
    ):
        _ = quarantine_inoperative_red_scope(change_id, slice_id, repo_root=repo_root)
        return SliceWorkflowReceiptEvaluation(False, "RECEIPT_NOT_ACCEPTED", ())

    paths_resolved = _resolve_work_paths(
        change_id,
        slice_id,
        stage,
        receipt,
        repo_root=repo_root,
    )
    if paths_resolved is None:
        return SliceWorkflowReceiptEvaluation(False, "STATUS_ONLY_STUB", ())

    if paths_resolved and not _all_work_files_present(repo_root, paths_resolved):
        return SliceWorkflowReceiptEvaluation(False, "FILE_MISSING", paths_resolved)

    if not paths_resolved:
        return SliceWorkflowReceiptEvaluation(True, None, (), receipt_status=None)

    base_state = dict(state) if state is not None else _state_for_digest(repo_root, change_id)
    base_state.setdefault("repository_path", str(repo_root.resolve()))
    base_state.setdefault("change_id", change_id)

    key = established_key(change_id, slice_id, stage)
    est_key = SliceEstablishedKey(key)
    check_receipt = build_slice_check_receipt(est_key, paths_resolved)

    stored = str(receipt.get(WORK_SCOPE_DIGEST_KEY) or "").strip()
    backfilled = False

    if not stored:
        new_digest, wrote = try_backfill_work_scope_digest(
            change_id,
            slice_id,
            stage,
            repo_root=repo_root,
            work_paths=paths_resolved,
            check_receipt=check_receipt,
        )
        if new_digest:
            stored = new_digest
            backfilled = wrote

    hydrated = hydrate_state_for_check(base_state, key, stored or None)
    check_status = check_receipt.check(hydrated)

    if check_status is CheckWorkflowReceiptStatus.MATCH:
        if block_active_repair_reuse and repair_blocks_receipt_reuse(
            state,
            change_id=change_id,
            slice_id=slice_id,
            stage=stage,
        ):
            return SliceWorkflowReceiptEvaluation(
                False,
                "ACTIVE_REPAIR_BLOCKS_REUSE",
                paths_resolved,
                backfilled_digest=backfilled,
                receipt_status=check_status,
            )
        if not stage_receipt_contract_accepted(
            change_id,
            slice_id,
            stage,
            receipt,
            repo_root=repo_root,
        ):
            if stage == STAGE_APPLY_RED:
                _ = quarantine_inoperative_red_scope(
                    change_id,
                    slice_id,
                    repo_root=repo_root,
                )
            return SliceWorkflowReceiptEvaluation(
                False,
                "RECEIPT_NOT_ACCEPTED",
                paths_resolved,
                backfilled_digest=backfilled,
                receipt_status=check_status,
            )
        return SliceWorkflowReceiptEvaluation(
            True,
            None,
            paths_resolved,
            backfilled_digest=backfilled,
            receipt_status=check_status,
        )
    if check_status is CheckWorkflowReceiptStatus.STALE:
        return SliceWorkflowReceiptEvaluation(
            False,
            "SCOPE_STALE",
            paths_resolved,
            backfilled_digest=backfilled,
            receipt_status=check_status,
        )
    if check_status is CheckWorkflowReceiptStatus.DIGEST_UNAVAILABLE:
        return SliceWorkflowReceiptEvaluation(
            False,
            "DIGEST_UNAVAILABLE",
            paths_resolved,
            backfilled_digest=backfilled,
            receipt_status=check_status,
        )
    return SliceWorkflowReceiptEvaluation(
        False,
        "SCOPE_NOT_ESTABLISHED",
        paths_resolved,
        backfilled_digest=backfilled,
        receipt_status=check_status,
    )


def persist_after_successful_hop(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
    state: Mapping[str, Any] | None = None,
) -> SliceWorkflowReceiptEvaluation:
    """POST gate: записать digest и перепроверить ``MATCH``.

    Повторная оценка не блокируется ``ACTIVE_REPAIR_BLOCKS_REUSE``: digest только
    что зафиксирован этим hop; флаг repair по-прежнему блокирует PRE/skip.
    """
    pre = evaluate_slice_stage(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
        state=state,
    )
    if not pre.work_paths and pre.valid:
        return pre

    paths = pre.work_paths
    if not paths:
        return SliceWorkflowReceiptEvaluation(False, "STATUS_ONLY_STUB", ())

    key = established_key(change_id, slice_id, stage)
    est_key = SliceEstablishedKey(key)
    check_receipt = build_slice_check_receipt(est_key, paths)
    digest = compute_work_scope_digest(
        check_receipt,
        repo_root=repo_root,
        change_id=change_id,
    )
    if not digest:
        return SliceWorkflowReceiptEvaluation(False, "DIGEST_UNAVAILABLE", paths)
    _ = persist_work_scope_digest(
        change_id,
        slice_id,
        stage,
        digest,
        repo_root=repo_root,
    )
    return evaluate_slice_stage(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
        state=state,
        block_active_repair_reuse=False,
    )
