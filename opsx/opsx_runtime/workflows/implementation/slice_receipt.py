"""Machine receipt apply-stage: пути, загрузка JSON, gate делегирует в ``slice_check_workflow_receipt``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from workflows.artifact_paths import (
    stage_receipt_path,
    stage_scope_service_receipt_path,
    useful_stage_receipt_path,
)
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)

_STAGE_RECEIPT_NAMES: dict[str, tuple[str, str]] = {
    STAGE_APPLY_SCAFFOLD: ("apply-scaffold", "SCAFFOLD.json"),
    STAGE_APPLY_RED: ("apply-red", "RED.json"),
    STAGE_APPLY_IMPLEMENT: ("apply-implement", "IMPLEMENTATION.json"),
    STAGE_APPLY_GREEN: ("apply-green", "GREEN.json"),
}

_SUCCESS_STATUSES: dict[str, frozenset[str]] = {
    STAGE_APPLY_SCAFFOLD: frozenset({"SCAFFOLD_READY"}),
    STAGE_APPLY_RED: frozenset({"RED_PROVEN", "ALREADY_SATISFIED"}),
    STAGE_APPLY_IMPLEMENT: frozenset({"IMPLEMENTATION_WRITTEN", "IMPLEMENTATION_ADOPTED"}),
    STAGE_APPLY_GREEN: frozenset({"GREEN_PROVEN"}),
}


@dataclass(frozen=True, slots=True)
class SliceReceiptValidation:
    """Результат детерминированной проверки рецепта."""

    valid: bool
    code: str | None
    work_paths: tuple[str, ...]


def normalize_work_path(path: str) -> str:
    """Канонический относительный путь репозитория (POSIX), одинаковый на Win/Linux."""
    from pathlib import PurePosixPath

    text = str(path or "").strip()
    if not text:
        return ""
    text = text.replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        return ""
    pure = PurePosixPath(text)
    if pure.is_absolute():
        return ""
    normalized = pure.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _posix(path: str) -> str:
    """Алиас для нормализации пути в receipt (тесты и внутренние вызовы)."""
    return normalize_work_path(path)


def receipt_json_path(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> Path:
    """Абсолютный путь к содержательному slice receipt (``openspec/changes``)."""
    return useful_stage_receipt_path(repo_root, change_id, stage, slice_id)


def stage_scope_overlay_path(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> Path:
    """Служебный overlay ``scope.receipt.json`` в ``opsx/receipts``."""
    return stage_scope_service_receipt_path(repo_root, change_id, stage, slice_id)


def _legacy_receipt_json_path(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> Path:
    """Алиас useful receipt (обратная совместимость тестов)."""
    return receipt_json_path(change_id, slice_id, stage, repo_root=repo_root)


def resolve_receipt_json_path(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> Path:
    """Канонический путь содержательного receipt в planning change."""
    return receipt_json_path(change_id, slice_id, stage, repo_root=repo_root)


def _merge_scope_overlay(
    data: dict[str, Any],
    *,
    change_id: str,
    slice_id: str,
    stage: str,
    repo_root: Path,
) -> dict[str, Any]:
    """Подмешать ``work_scope_digest`` из service overlay без дублирования useful JSON."""
    from workflows.implementation.slice_check_workflow_receipt import WORK_SCOPE_DIGEST_KEY

    overlay_path = stage_scope_overlay_path(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
    )
    if not overlay_path.is_file():
        return data
    overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
    if not isinstance(overlay, dict):
        return data
    merged = dict(data)
    digest = overlay.get(WORK_SCOPE_DIGEST_KEY)
    if isinstance(digest, str) and digest.strip():
        merged[WORK_SCOPE_DIGEST_KEY] = digest.strip()
    return merged


def load_stage_receipt(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    """Прочитать useful receipt; overlay digest из ``opsx/receipts``; legacy fallback."""
    useful = useful_stage_receipt_path(repo_root, change_id, stage, slice_id)
    if useful.is_file():
        data = json.loads(useful.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _merge_scope_overlay(
                data,
                change_id=change_id,
                slice_id=slice_id,
                stage=stage,
                repo_root=repo_root,
            )
        return None
    legacy = stage_receipt_path(repo_root, change_id, stage, slice_id)
    if legacy.is_file():
        data = json.loads(legacy.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    return None


def work_paths_from_receipt(stage: str, receipt: Mapping[str, Any]) -> list[str]:
    """Минимальный список путей файлов работы для стадии."""
    if stage == STAGE_APPLY_SCAFFOLD:
        created = receipt.get("created_paths") or []
        extended = receipt.get("extended_paths") or []
        paths = [_posix(str(p)) for p in (*created, *extended) if p]
        return sorted(set(paths))
    if stage == STAGE_APPLY_RED:
        tests = [_posix(str(p)) for p in (receipt.get("test_paths") or []) if p]
        fixtures = [_posix(str(p)) for p in (receipt.get("fixture_paths") or []) if p]
        return sorted(set([*tests, *fixtures]))
    if stage == STAGE_APPLY_IMPLEMENT:
        modified = [_posix(str(p)) for p in (receipt.get("production_paths_modified") or []) if p]
        created = [_posix(str(p)) for p in (receipt.get("production_paths_created") or []) if p]
        return sorted(set([*modified, *created]))
    if stage == STAGE_APPLY_GREEN:
        raise ValueError("use green_work_paths() for APPLY_GREEN")
    raise ValueError(f"unsupported stage for work paths: {stage!r}")


def green_work_paths(
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
) -> list[str]:
    """Пути тестов красного и продуктовых файлов кода для зелёного."""
    red = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=repo_root)
    impl = load_stage_receipt(
        change_id,
        slice_id,
        STAGE_APPLY_IMPLEMENT,
        repo_root=repo_root,
    )
    paths: list[str] = []
    if isinstance(red, dict):
        paths.extend(work_paths_from_receipt(STAGE_APPLY_RED, red))
    if isinstance(impl, dict):
        paths.extend(work_paths_from_receipt(STAGE_APPLY_IMPLEMENT, impl))
    return sorted(set(paths))


def validate_stage_receipt(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> SliceReceiptValidation:
    """Детерминированная проверка через ``CheckWorkflowReceipt`` (``work_scope_digest``)."""
    from workflows.implementation.slice_check_workflow_receipt import evaluate_slice_stage

    evaluated = evaluate_slice_stage(
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
    )
    return SliceReceiptValidation(
        evaluated.valid,
        evaluated.code,
        evaluated.work_paths,
    )


def write_work_fingerprints(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Записать ``work_scope_digest`` (legacy имя функции для тестов POST-hop)."""
    from workflows.implementation.slice_check_workflow_receipt import (
        WORK_SCOPE_DIGEST_KEY,
        build_slice_check_receipt,
        compute_work_scope_digest,
        established_key,
        persist_work_scope_digest,
        SliceEstablishedKey,
    )

    receipt = load_stage_receipt(change_id, slice_id, stage, repo_root=repo_root)
    if receipt is None:
        raise FileNotFoundError(
            f"receipt missing for {change_id}/{slice_id}/{stage}",
        )
    if stage == STAGE_APPLY_GREEN:
        paths = green_work_paths(change_id, slice_id, repo_root=repo_root)
    else:
        paths = work_paths_from_receipt(stage, receipt)
    normalized = tuple(
        sorted({p for p in (_posix(str(x)) for x in paths) if p})
    )
    if not normalized:
        return dict(receipt)
    key = SliceEstablishedKey(established_key(change_id, slice_id, stage))
    check_receipt = build_slice_check_receipt(key, normalized)
    digest = compute_work_scope_digest(
        check_receipt,
        repo_root=repo_root,
        change_id=change_id,
    )
    if not digest:
        raise ValueError("work scope digest unavailable")
    payload = persist_work_scope_digest(
        change_id,
        slice_id,
        stage,
        digest,
        repo_root=repo_root,
    )
    if WORK_SCOPE_DIGEST_KEY not in payload:
        raise ValueError("work_scope_digest not written")
    return payload
