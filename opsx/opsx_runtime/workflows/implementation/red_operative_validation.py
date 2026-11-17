"""Машинная проверка RED.json через ``opsx-apply-red.mjs`` (вне SDK shell)."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repo_paths import resolve_opsx_repository_root

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RedOperativeValidation:
    """Результат operative gate для RED receipt."""

    ok: bool
    red_json_path: Path
    exit_code: int
    status: str | None
    errors: list[dict[str, Any]]
    raw_stdout: str
    raw_stderr: str
    live_pytest_skipped: bool = False
    live_pytest_skip_reason: str | None = None


def slice_red_json_path(
    change_id: str,
    *,
    repo_root: Path,
    slice_id: str,
) -> Path:
    """Путь к ``apply-red/<slice>/RED.json`` для change."""
    from workflows.implementation.routing_contract import STAGE_APPLY_RED
    from workflows.implementation.slice_receipt import resolve_receipt_json_path

    repo_root = resolve_opsx_repository_root(repo_root)
    return resolve_receipt_json_path(
        change_id,
        slice_id,
        STAGE_APPLY_RED,
        repo_root=repo_root,
    )


def _log_immutable_red_reuse(
    *,
    change_id: str | None,
    slice_id: str | None,
    red_json: Path,
    skip_reason: str | None,
) -> None:
    """Structured log: operative rewalk без повторного live pytest."""
    _LOGGER.info(
        (
            "slice_hop_check_done change_id=%s slice_id=%s stage=APPLY_RED "
            "operation=validate_red_operative live_pytest_skipped=true "
            "reason=%s red_json=%s"
        ),
        change_id or "",
        slice_id or "",
        skip_reason or "test_content_identity_unchanged",
        red_json,
    )


def validate_red_operative(
    red_json: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 180,
    change_id: str | None = None,
    slice_id: str | None = None,
) -> RedOperativeValidation:
    """Запустить Node validator; не зависит от SDK EPERM на terminal logs.

    При неизменных байтах frozen-тестов и валидном ``operative_red_transition``
    transition-gate не вызывает live pytest (ответ ``live_pytest_skipped``).
    """
    repo_root = resolve_opsx_repository_root(repo_root)
    script = resource_path(repo_root, "opsx_tools", "opsx-apply-red.mjs")
    if not script.is_file():
        msg = f"opsx-apply-red.mjs missing: {script}"
        raise RuntimeError(msg)
    red_json = red_json.resolve()
    if not red_json.is_file():
        return RedOperativeValidation(
            ok=False,
            red_json_path=red_json,
            exit_code=2,
            status="MISSING",
            errors=[{"code": "MISSING_RED", "message": str(red_json)}],
            raw_stdout="",
            raw_stderr="",
        )
    if not change_id or not slice_id:
        try:
            body = json.loads(red_json.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                change_id = change_id or str(body.get("change_id") or "").strip() or None
                slice_id = slice_id or str(body.get("slice_id") or "").strip() or None
        except (OSError, json.JSONDecodeError):
            pass
    try:
        rel = red_json.relative_to(repo_root)
        red_arg = rel.as_posix()
    except ValueError:
        red_arg = str(red_json)
    hook = Path(__file__).resolve().with_name("receipt_cli_identity_hook.mjs")
    if hook.is_file():
        cmd = ["node", "--import", hook.as_uri(), str(script), red_arg]
    else:
        cmd = ["node", str(script), red_arg]
    from agents.agent_tools.deterministic.pytest_tool import (
        frozen_command_subprocess_env,
    )

    proc = run_subprocess_utf8(
        cmd,
        cwd=repo_root,
        timeout=timeout_sec,
        base_env=frozen_command_subprocess_env(repo_root),
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    status: str | None = None
    errors: list[dict[str, Any]] = []
    live_pytest_skipped = False
    live_pytest_skip_reason: str | None = None
    if stdout.strip():
        try:
            body = json.loads(stdout)
            if isinstance(body, dict):
                status = str(body.get("status") or "") or None
                raw_errors = body.get("errors")
                if isinstance(raw_errors, list):
                    errors = [e for e in raw_errors if isinstance(e, dict)]
                if body.get("live_pytest_skipped") is True:
                    live_pytest_skipped = True
                    raw_reason = body.get("live_pytest_skip_reason")
                    if isinstance(raw_reason, str) and raw_reason.strip():
                        live_pytest_skip_reason = raw_reason.strip()
        except json.JSONDecodeError:
            errors = [{"code": "INVALID_JSON", "message": stdout[:500]}]
    ok = proc.returncode == 0 and status == "PASS"
    if live_pytest_skipped and ok:
        _log_immutable_red_reuse(
            change_id=change_id,
            slice_id=slice_id,
            red_json=red_json,
            skip_reason=live_pytest_skip_reason,
        )
    return RedOperativeValidation(
        ok=ok,
        red_json_path=red_json,
        exit_code=proc.returncode,
        status=status,
        errors=errors,
        raw_stdout=stdout,
        raw_stderr=stderr,
        live_pytest_skipped=live_pytest_skipped,
        live_pytest_skip_reason=live_pytest_skip_reason,
    )


def quarantine_inoperative_red_scope(
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
) -> bool:
    """Снять reuse digest у RED, если durable receipt не проходит ``opsx-apply-red.mjs``.

    Не меняет ``status`` в useful JSON: только overlay ``work_scope_digest``, чтобы
    resume/checkpoint не трактовал illegal ALREADY_SATISFIED как MATCH-skip.
    """
    from workflows.implementation.routing_contract import STAGE_APPLY_RED
    from workflows.implementation.slice_receipt import (
        load_stage_receipt,
        receipt_json_path,
        stage_scope_overlay_path,
    )

    overlay_path = stage_scope_overlay_path(
        change_id,
        slice_id,
        STAGE_APPLY_RED,
        repo_root=repo_root,
    )
    if not overlay_path.is_file():
        return False
    receipt = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=repo_root)
    if receipt is None:
        return False
    status = str(receipt.get("status") or "")
    if status not in ("RED_PROVEN", "ALREADY_SATISFIED"):
        return False
    try:
        resolved_root = resolve_opsx_repository_root(repo_root)
    except ValueError:
        return False
    validator = resolved_root / "opsx_tools" / "opsx-apply-red.mjs"
    if not validator.is_file():
        return False
    red_path = receipt_json_path(
        change_id,
        slice_id,
        STAGE_APPLY_RED,
        repo_root=resolved_root,
    )
    if validate_red_operative(red_path, repo_root=resolved_root).ok:
        return False
    overlay_path.unlink()
    return True
