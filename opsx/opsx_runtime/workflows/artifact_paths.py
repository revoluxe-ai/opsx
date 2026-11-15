"""Канонические пути runtime-артефактов OPSX (логи, receipts, planning).

Planning и содержательные результаты (в git): ``openspec/changes/<id>/`` —
proposal, design, tasks, specs, ``apply-design/APPLY-DESIGN.json``,
``scenario-coverage/plan.md`` и ``scenarios/*.md``.

Receipts (вне git): ``opsx/receipts/<id>/`` — только подтверждения идемпотентности
и машинные журналы проверок, не копии APPLY-DESIGN.

Логи: ``opsx/logs/<id>/``. Диалоги: ``opsx/runs/dialogs/``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_LEGACY_SAFE_CHANGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LEGACY_VERIFY_STREAM = re.compile(
    r"impl-verify-([0-9a-f]{12})\.",
    re.IGNORECASE,
)
_SESSION_FROM_RUN_LOG = re.compile(r"/([0-9a-f]{12})\.log$", re.IGNORECASE)

_APPLY_DESIGN_DIR = "apply-design"
_APPLY_DESIGN_REVIEW_DIR = "apply-design-review"
_APPLY_SCAFFOLD_DIR = "apply-scaffold"
_APPLY_RED_DIR = "apply-red"
_APPLY_IMPLEMENT_DIR = "apply-implement"
_APPLY_GREEN_DIR = "apply-green"
_SCENARIO_COVERAGE_RECEIPTS_DIR = "scenario-coverage"

_STAGE_FOLDER: dict[str, tuple[str, str]] = {
    "apply_scaffold": (_APPLY_SCAFFOLD_DIR, "SCAFFOLD.json"),
    "apply_red": (_APPLY_RED_DIR, "RED.json"),
    "apply_implement": (_APPLY_IMPLEMENT_DIR, "IMPLEMENTATION.json"),
    "apply_green": (_APPLY_GREEN_DIR, "GREEN.json"),
}

_STAGE_FOLDER_BY_ROUTING: dict[str, tuple[str, str]] = {
    "APPLY_SCAFFOLD": (_APPLY_SCAFFOLD_DIR, "SCAFFOLD.json"),
    "APPLY_RED": (_APPLY_RED_DIR, "RED.json"),
    "APPLY_IMPLEMENT": (_APPLY_IMPLEMENT_DIR, "IMPLEMENTATION.json"),
    "APPLY_GREEN": (_APPLY_GREEN_DIR, "GREEN.json"),
}


def _assert_safe_change_id(change_id: str) -> None:
    if not _LEGACY_SAFE_CHANGE.fullmatch(change_id) or change_id in (".", ".."):
        raise ValueError("CHANGE_ID_UNSAFE")


def planning_change_dir(repo_root: Path, change_id: str) -> Path:
    """Каталог planning change: ``openspec/changes/<change_id>/``."""
    _assert_safe_change_id(change_id)
    return repo_root.resolve() / "openspec" / "changes" / change_id


def logs_dir(repo_root: Path, change_id: str) -> Path:
    """Каталог логов одного change: ``opsx/logs/<change_id>/``."""
    _assert_safe_change_id(change_id)
    return repo_root.resolve() / "opsx" / "logs" / change_id


def run_log_path(repo_root: Path, change_id: str, session_id: str) -> Path:
    """Единственный полный лог CLI-запуска: ``opsx/logs/<change_id>/<session_id>.log``."""
    _assert_safe_change_id(change_id)
    sid = session_id.strip()
    if not sid or not re.fullmatch(r"[0-9a-f]{8,32}", sid, re.IGNORECASE):
        raise ValueError("RUN_LOG_SESSION_INVALID")
    return logs_dir(repo_root, change_id) / f"{sid}.log"


def receipts_dir(repo_root: Path, change_id: str) -> Path:
    """Корень machine receipts change: ``opsx/receipts/<change_id>/``."""
    _assert_safe_change_id(change_id)
    return repo_root.resolve() / "opsx" / "receipts" / change_id


def impl_verify_receipt_path(repo_root: Path, change_id: str) -> Path:
    """``IMPL-VERIFY-RECEIPT.json`` под ``opsx/receipts``."""
    return receipts_dir(repo_root, change_id) / "IMPL-VERIFY-RECEIPT.json"


def legacy_verify_dir(repo_root: Path, change_id: str) -> Path:
    """Legacy openspec verify (только миграция)."""
    _assert_safe_change_id(change_id)
    return planning_change_dir(repo_root, change_id) / "verify"


def resolve_impl_verify_receipt_path(repo_root: Path, change_id: str) -> Path | None:
    """Receipt impl-verify: только канонический путь ``opsx/receipts``."""
    canon = impl_verify_receipt_path(repo_root, change_id)
    if canon.is_file():
        return canon
    return None


def apply_design_artifact_path(repo_root: Path, change_id: str) -> Path:
    """Содержательный ``APPLY-DESIGN.json`` в change, не receipt."""
    return planning_change_dir(repo_root, change_id) / _APPLY_DESIGN_DIR / "APPLY-DESIGN.json"


def apply_design_artifact_md_path(repo_root: Path, change_id: str) -> Path:
    """Сопроводительный ``APPLY-DESIGN.md`` рядом с каноническим JSON."""
    return planning_change_dir(repo_root, change_id) / _APPLY_DESIGN_DIR / "APPLY-DESIGN.md"


def apply_design_hop_receipt_path(repo_root: Path, change_id: str) -> Path:
    """Служебный receipt идемпотентности APPLY_DESIGN."""
    return receipts_dir(repo_root, change_id) / _APPLY_DESIGN_DIR / "hop.receipt.json"


def apply_design_review_artifact_path(repo_root: Path, change_id: str) -> Path:
    """Содержательный ``APPLY-DESIGN-REVIEW.json`` в change."""
    return (
        planning_change_dir(repo_root, change_id)
        / _APPLY_DESIGN_REVIEW_DIR
        / "APPLY-DESIGN-REVIEW.json"
    )


def apply_design_review_receipt_path(repo_root: Path, change_id: str) -> Path:
    """Канонический review JSON. Имя сохранено для существующих вызывающих."""
    return apply_design_review_artifact_path(repo_root, change_id)


def apply_design_review_hop_receipt_path(repo_root: Path, change_id: str) -> Path:
    """Служебный receipt идемпотентности design review."""
    return receipts_dir(repo_root, change_id) / _APPLY_DESIGN_REVIEW_DIR / "hop.receipt.json"


def useful_stage_receipt_path(
    repo_root: Path,
    change_id: str,
    stage: str,
    slice_id: str,
) -> Path:
    """Содержательный slice receipt в planning change (``openspec/changes/...``)."""
    folder, filename = _STAGE_FOLDER_BY_ROUTING.get(stage) or _stage_folder_filename(stage)
    return planning_change_dir(repo_root, change_id) / folder / slice_id / filename


def stage_scope_service_receipt_path(
    repo_root: Path,
    change_id: str,
    stage: str,
    slice_id: str,
) -> Path:
    """Служебный overlay: ``work_scope_digest`` в ``opsx/receipts`` (не копия useful)."""
    folder, _ = _STAGE_FOLDER_BY_ROUTING.get(stage) or _stage_folder_filename(stage)
    return receipts_dir(repo_root, change_id) / folder / slice_id / "scope.receipt.json"


def stage_receipt_path(
    repo_root: Path,
    change_id: str,
    stage: str,
    slice_id: str,
) -> Path:
    """Legacy полный receipt в ``opsx/receipts`` (миграция; не дублировать useful)."""
    folder, filename = _STAGE_FOLDER_BY_ROUTING.get(stage) or _stage_folder_filename(stage)
    return receipts_dir(repo_root, change_id) / folder / slice_id / filename


def stage_receipt_md_path(
    repo_root: Path,
    change_id: str,
    stage: str,
    slice_id: str,
) -> Path:
    """Человеческий ``*.md`` рядом с JSON стадии слайса."""
    folder, json_name = _STAGE_FOLDER_BY_ROUTING.get(stage) or _stage_folder_filename(stage)
    stem = json_name[:-5] if json_name.endswith(".json") else json_name
    return receipts_dir(repo_root, change_id) / folder / slice_id / f"{stem}.md"


def stage_receipt_relpos(repo_root: Path, path: Path) -> str:
    """POSIX путь от корня репо для ``machine_path`` в receipt."""
    rel = path.resolve().relative_to(repo_root.resolve())
    return rel.as_posix()


def _stage_folder_filename(stage: str) -> tuple[str, str]:
    if stage in _STAGE_FOLDER:
        return _STAGE_FOLDER[stage]
    if stage in _STAGE_FOLDER_BY_ROUTING:
        return _STAGE_FOLDER_BY_ROUTING[stage]
    text = stage.replace("_", "-")
    if text.startswith("apply-"):
        folder = text
        name = {
            "apply-scaffold": "SCAFFOLD.json",
            "apply-red": "RED.json",
            "apply-implement": "IMPLEMENTATION.json",
            "apply-green": "GREEN.json",
        }.get(folder, "receipt.json")
        return folder, name
    raise ValueError(f"UNKNOWN_STAGE_RECEIPT: {stage}")


def scenario_coverage_receipts_dir(repo_root: Path, change_id: str) -> Path:
    """``opsx/receipts/<id>/scenario-coverage/``."""
    return receipts_dir(repo_root, change_id) / _SCENARIO_COVERAGE_RECEIPTS_DIR


def scenario_machine_receipt_path(repo_root: Path, change_id: str, filename: str) -> Path:
    """Один machine-файл scenario-coverage (plan.receipt.json, row_walk_journal.json, …)."""
    name = filename.strip().lstrip("/").replace("\\", "/")
    if "/" in name or name.startswith(".."):
        raise ValueError("SCENARIO_RECEIPT_NAME_UNSAFE")
    return scenario_coverage_receipts_dir(repo_root, change_id) / name


def scenario_coverage_planning_dir(repo_root: Path, change_id: str) -> Path:
    """Planning scenario-coverage: ``openspec/changes/<id>/scenario-coverage/``."""
    return planning_change_dir(repo_root, change_id) / "scenario-coverage"


def session_id_from_stream_log(stream_log: str) -> str | None:
    """Session id из нового ``…/<session>.log`` или legacy ``impl-verify-<session>.``."""
    text = stream_log.replace("\\", "/")
    match = _SESSION_FROM_RUN_LOG.search(text)
    if match:
        return match.group(1).lower()
    match = _LEGACY_VERIFY_STREAM.search(text)
    if match:
        return match.group(1).lower()
    return None


def purge_change_runtime_artifacts(repo_root: Path, change_id: str) -> None:
    """Удалить логи и receipts change (после archive). Planning в openspec не трогаем."""
    _assert_safe_change_id(change_id)
    root = repo_root.resolve()
    for directory in (
        logs_dir(root, change_id),
        receipts_dir(root, change_id),
    ):
        if directory.is_symlink():
            continue
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
