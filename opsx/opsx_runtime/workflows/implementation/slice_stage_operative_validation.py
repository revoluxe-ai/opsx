"""Машинная проверка slice stage receipts через Node (вне SDK shell).

Скрипты в ``opsx_tools`` вызывает только runtime Python, не hop-модель.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflows.artifact_paths import (
    apply_design_review_receipt_path,
    stage_receipt_path,
)
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt import resolve_receipt_json_path
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repo_paths import resolve_opsx_repository_root


@dataclass(frozen=True, slots=True)
class StageOperativeValidation:
    """Результат operative gate для stage receipt JSON."""

    ok: bool
    receipt_path: Path
    exit_code: int
    status: str | None
    errors: list[dict[str, Any]]
    raw_stdout: str
    raw_stderr: str


def slice_scaffold_json_path(
    change_id: str,
    *,
    repo_root: Path,
    slice_id: str,
) -> Path:
    """Путь к ``apply-scaffold/<slice>/SCAFFOLD.json`` (канон + legacy fallback)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    return resolve_receipt_json_path(
        change_id,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo_root,
    )


def slice_implement_json_path(
    change_id: str,
    *,
    repo_root: Path,
    slice_id: str,
) -> Path:
    """Путь к ``apply-implement/<slice>/IMPLEMENTATION.json``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    return resolve_receipt_json_path(
        change_id,
        slice_id,
        STAGE_APPLY_IMPLEMENT,
        repo_root=repo_root,
    )


def slice_green_json_path(
    change_id: str,
    *,
    repo_root: Path,
    slice_id: str,
) -> Path:
    """Путь к ``apply-green/<slice>/GREEN.json``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    return resolve_receipt_json_path(
        change_id,
        slice_id,
        STAGE_APPLY_GREEN,
        repo_root=repo_root,
    )


def apply_design_review_json_path(change_id: str, *, repo_root: Path) -> Path:
    """Путь к ``apply-design-review/APPLY-DESIGN-REVIEW.json``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    return apply_design_review_receipt_path(repo_root, change_id)


def _repo_relative_arg(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _run_stage_validator(
    *,
    script_rel: str,
    receipt_json: Path,
    repo_root: Path,
    timeout_sec: int,
    missing_code: str,
) -> StageOperativeValidation:
    """Общий subprocess wrapper для ``opsx-apply-*.mjs`` CLI validators."""
    repo_root = resolve_opsx_repository_root(repo_root)
    script = resource_path(repo_root, "opsx_tools", script_rel)
    if not script.is_file():
        msg = f"{script_rel} missing: {script}"
        raise RuntimeError(msg)
    receipt_json = receipt_json.resolve()
    if not receipt_json.is_file():
        return StageOperativeValidation(
            ok=False,
            receipt_path=receipt_json,
            exit_code=2,
            status="MISSING",
            errors=[{"code": missing_code, "message": str(receipt_json)}],
            raw_stdout="",
            raw_stderr="",
        )
    cmd = ["node", str(script), _repo_relative_arg(receipt_json, repo_root)]
    proc = run_subprocess_utf8(cmd, cwd=repo_root, timeout=timeout_sec)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    status: str | None = None
    errors: list[dict[str, Any]] = []
    if stdout.strip():
        try:
            body = json.loads(stdout)
            if isinstance(body, dict):
                status = str(body.get("status") or "") or None
                raw_errors = body.get("errors")
                if isinstance(raw_errors, list):
                    errors = [e for e in raw_errors if isinstance(e, dict)]
        except json.JSONDecodeError:
            errors = [{"code": "INVALID_JSON", "message": stdout[:500]}]
    ok = proc.returncode == 0 and status == "PASS"
    if not ok and not errors and stderr.strip():
        errors = [{"code": "SUBPROCESS", "message": stderr.strip()[:500]}]
    return StageOperativeValidation(
        ok=ok,
        receipt_path=receipt_json,
        exit_code=proc.returncode,
        status=status,
        errors=errors,
        raw_stdout=stdout,
        raw_stderr=stderr,
    )


def validate_scaffold_operative(
    scaffold_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 180,
) -> StageOperativeValidation:
    """Запустить ``opsx-apply-scaffold.mjs`` для on-disk SCAFFOLD.json."""
    return _run_stage_validator(
        script_rel="opsx-apply-scaffold.mjs",
        receipt_json=scaffold_json,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
        missing_code="MISSING_SCAFFOLD",
    )


def validate_scaffold_operative_projected(
    scaffold_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 180,
) -> StageOperativeValidation:
    """Проверить SCAFFOLD.json с projected ``work_scope_digest`` (как lane gate POST-hop)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    scaffold_json = scaffold_json.resolve()
    if not scaffold_json.is_file():
        return validate_scaffold_operative(
            scaffold_json,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
        )
    try:
        raw = json.loads(scaffold_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return validate_scaffold_operative(
            scaffold_json,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
        )
    if not isinstance(raw, dict):
        return validate_scaffold_operative(
            scaffold_json,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
        )
    from workflows.implementation.slice_check_workflow_receipt import (
        project_scaffold_receipt_for_operative_gate,
    )

    projected = project_scaffold_receipt_for_operative_gate(raw, repo_root=repo_root)
    if projected == raw:
        return validate_scaffold_operative(
            scaffold_json,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
        )
    fd, tmp_name = tempfile.mkstemp(
        suffix=".scaffold-operative-gate.json",
        dir=str(scaffold_json.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(projected, indent=2, ensure_ascii=False))
            _ = handle.write("\n")
        return _run_stage_validator(
            script_rel="opsx-apply-scaffold.mjs",
            receipt_json=tmp_path,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
            missing_code="MISSING_SCAFFOLD",
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_implement_operative(
    implement_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 180,
) -> StageOperativeValidation:
    """Запустить ``opsx-apply-implement.mjs`` для on-disk IMPLEMENTATION.json."""
    return _run_stage_validator(
        script_rel="opsx-apply-implement.mjs",
        receipt_json=implement_json,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
        missing_code="MISSING_IMPLEMENTATION",
    )


def validate_green_operative(
    green_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 300,
) -> StageOperativeValidation:
    """Запустить ``opsx-apply-green.mjs`` (JSON validator only, без test runner)."""
    return _run_stage_validator(
        script_rel="opsx-apply-green.mjs",
        receipt_json=green_json,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
        missing_code="MISSING_GREEN",
    )


def validate_design_review_operative(
    review_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 120,
) -> StageOperativeValidation:
    """Запустить ``opsx-apply-design-review.mjs`` для APPLY-DESIGN-REVIEW.json."""
    return _run_stage_validator(
        script_rel="opsx-apply-design-review.mjs",
        receipt_json=review_json,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
        missing_code="MISSING_DESIGN_REVIEW",
    )
