"""Машинная проверка APPLY-DESIGN.json (Node opsx-apply-design.mjs)."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import subprocess

from workflows.implementation.node_subprocess import run_subprocess_utf8


@dataclass(frozen=True)
class DesignMachineGateResult:
    """Результат ``opsx-apply-design.mjs`` для on-disk APPLY-DESIGN."""

    ok: bool
    status: str
    exit_code: int
    errors: list[dict[str, object]]


@dataclass(frozen=True)
class DesignDependentSnapshotResult:
    """Сверка snapshot файлов, от которых зависит APPLY-DESIGN, с диском.

    Считает не этот модуль: вызывается Node ``evaluateApplyDesignFreshnessFromDisk``
    (``classifyApplyDesignPlanningFreshness`` + protected context).
    """

    ok: bool
    code: str | None
    frozen_digest: str | None
    current_digest: str | None


_PROTECTED_CONTEXT_DRIFT = "PROTECTED_CONTEXT_DRIFT"


_APPLY_DESIGN_SNAPSHOT_SIGNER_SCRIPT = """
import { readFileSync } from 'node:fs';
const { evaluateApplyDesignFreshnessFromDisk } = await import((await import("node:url")).pathToFileURL((await import("node:path")).resolve(process.env.OPSX_CORE_ROOT || process.cwd(), 'opsx_tools/opsx-apply-pipeline-identity.mjs')).href);
const designPath = process.argv[1];
const repoRoot = process.argv[2];
const design = JSON.parse(readFileSync(designPath, 'utf8'));
const result = evaluateApplyDesignFreshnessFromDisk(design, { repoRoot });
console.log(JSON.stringify({
  ok: Boolean(result && result.ok),
  code: result && result.code ? result.code : null,
  frozen_digest: result && result.frozen_digest ? result.frozen_digest : null,
  current_digest: result && result.current_digest ? result.current_digest : null,
}));
"""


def _parse_snapshot_signer_stdout(proc: subprocess.CompletedProcess[str]) -> DesignDependentSnapshotResult:
    """Разобрать stdout Node snapshot signer в ``DesignDependentSnapshotResult``."""
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "snapshot signer failed").strip()[:500]
        return DesignDependentSnapshotResult(
            ok=False,
            code="DESIGN_SNAPSHOT_SIGNER_FAILED",
            frozen_digest=None,
            current_digest=message or None,
        )
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return DesignDependentSnapshotResult(
            ok=False,
            code="DESIGN_SNAPSHOT_SIGNER_FAILED",
            frozen_digest=None,
            current_digest=proc.stdout.strip()[:500] or None,
        )
    if not isinstance(body, dict):
        return DesignDependentSnapshotResult(
            ok=False,
            code="DESIGN_SNAPSHOT_SIGNER_FAILED",
            frozen_digest=None,
            current_digest=None,
        )
    return DesignDependentSnapshotResult(
        ok=bool(body.get("ok")),
        code=str(body["code"]) if body.get("code") else None,
        frozen_digest=str(body["frozen_digest"]) if body.get("frozen_digest") else None,
        current_digest=str(body["current_digest"]) if body.get("current_digest") else None,
    )


def _run_apply_design_snapshot_signer(
    design_json_path: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 60,
) -> DesignDependentSnapshotResult:
    """Запустить Node snapshot signer, читая design с диска (без JSON в argv).

    На Windows длинное тело в командной строке даёт WinError 206; в argv только путь.
    """
    proc = run_subprocess_utf8(
        [
            "node",
            "--input-type=module",
            "-e",
            _APPLY_DESIGN_SNAPSHOT_SIGNER_SCRIPT,
            str(design_json_path),
            str(repo_root),
        ],
        cwd=repo_root,
        timeout=timeout_sec,
    )
    return _parse_snapshot_signer_stdout(proc)


def apply_design_json_path(change_id: str, *, repo_root: Path) -> Path:
    """Путь к APPLY-DESIGN.json для change."""
    from workflows.artifact_paths import apply_design_artifact_path

    return apply_design_artifact_path(repo_root, change_id)


def accepted_slice_contract_anchor_text(
    change_id: str,
    *,
    repo_root: Path,
) -> str:
    """Краткий якорь frozen test_paths по accepted slices (для DESIGN repair prompt)."""
    rel_root = resource_path(repo_root, "openspec", "changes", change_id)
    if not rel_root.is_dir():
        return ""
    script = f"""
    import {{ readFileSync }} from 'fs';
    import {{ resolve }} from 'path';
    const root = {json.dumps(str(repo_root))};
    const changeRoot = 'openspec/changes/{change_id}';
    const {{ loadChangeReceiptMaps }} =
      await import((await import("node:url")).pathToFileURL((await import("node:path")).resolve(process.env.OPSX_CORE_ROOT || process.cwd(), 'opsx_tools/opsx-apply-pipeline-identity.mjs')).href);
    const {{ classifyDesignSliceContractPhase }} =
      await import((await import("node:url")).pathToFileURL((await import("node:path")).resolve(process.env.OPSX_CORE_ROOT || process.cwd(), 'opsx_tools/opsx-apply-red-oracle-authority.mjs')).href);
    const design = JSON.parse(readFileSync(resolve(root, changeRoot, 'apply-design/APPLY-DESIGN.json'), 'utf8'));
    const sliceIds = (design.slice_order?.nodes || design.slices?.map(s => s.slice_id) || []).filter(Boolean);
    const maps = loadChangeReceiptMaps(root, changeRoot, sliceIds);
  const lines = [];
    for (const id of sliceIds) {{
      if (classifyDesignSliceContractPhase(id, design, {{ maps, repoRoot: root }}) !== 'accepted') continue;
      const red = maps.redBySlice?.[id];
      if (!red) continue;
      const paths = (red.test_paths || []).join(', ');
      lines.push(`${{id}}: frozen red_test_targets include [${{paths}}]`);
    }}
    console.log(lines.join('\\n'));
    """
    proc = run_subprocess_utf8(
        ["node", "--input-type=module", "-e", script],
        cwd=repo_root,
        timeout=60,
    )
    if proc.returncode != 0:
        return proc.stderr.strip()[:500]
    return proc.stdout.strip()


def evaluate_apply_design_dependent_snapshot(
    change_id: str,
    *,
    repo_root: Path,
    timeout_sec: int = 60,
) -> DesignDependentSnapshotResult:
    """Проверить, что snapshot зависимых файлов design ещё совпадает с диском.

    Нет файла или расхождение digest → design невалиден, hop design нужно выполнить.
    Алгоритм хеша не дублируется: только существующий Node signer.

    ``ok`` остаётся строгим (PROTECTED_CONTEXT_DRIFT → ``ok=False``). Для idempotent
    skip design lane см. ``design_lane_reuse_ok_after_dependent_snapshot``.
    """
    design_path = apply_design_json_path(change_id, repo_root=repo_root)
    if not design_path.is_file():
        return DesignDependentSnapshotResult(
            ok=False,
            code="APPLY_DESIGN_MISSING",
            frozen_digest=None,
            current_digest=None,
        )
    return _run_apply_design_snapshot_signer(
        design_path,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
    )


def protected_context_content_digest_drift_only(
    snapshot: DesignDependentSnapshotResult,
) -> bool:
    """True, если единственная причина ``snapshot.ok is False`` — drift protected content_digest."""
    if snapshot.ok:
        return False
    return snapshot.code == _PROTECTED_CONTEXT_DRIFT


def design_lane_reuse_ok_after_dependent_snapshot(
    snapshot: DesignDependentSnapshotResult,
    *,
    machine_gate_ok: bool,
) -> bool:
    """Можно ли переиспользовать APPLY-DESIGN без design-агента по snapshot+gate.

    При ``PROTECTED_CONTEXT_DRIFT`` planning на диске уже свежий (Node проверил
    planning раньше protected). Пересчёт digest protected-файлов — runtime SHA-256
    (opsx §10.4); ``APPLY-DESIGN.json`` не переписываем, если review свежесть
    привязана к ``artifact_sha256`` всего файла — stored ``content_digest`` остаётся
    исторической меткой, skip опирается на актуальный digest с диска через gate PASS.

    Любой другой код snapshot или ``machine_gate_ok=False`` → design hop обязателен.
    """
    if snapshot.ok:
        return True
    if not machine_gate_ok:
        return False
    return protected_context_content_digest_drift_only(snapshot)


def validate_apply_design_machine(
    change_id: str,
    *,
    repo_root: Path,
    timeout_sec: int = 120,
) -> DesignMachineGateResult:
    """Запустить Node-валидатор APPLY-DESIGN."""
    design_path = apply_design_json_path(change_id, repo_root=repo_root)
    rel = design_path.relative_to(repo_root).as_posix()
    script = resource_path(repo_root, "opsx_tools", "opsx-apply-design.mjs")
    proc = run_subprocess_utf8(
        ["node", str(script), "--repo", str(repo_root), rel],
        cwd=repo_root,
        timeout=timeout_sec,
    )
    errors: list[dict[str, object]] = []
    status = "UNKNOWN"
    if proc.stdout.strip():
        try:
            body = json.loads(proc.stdout)
            if isinstance(body, dict):
                status = str(body.get("status") or body.get("artifact_status") or status)
                raw_errors = body.get("errors")
                if isinstance(raw_errors, list):
                    errors = [e for e in raw_errors if isinstance(e, dict)]
        except json.JSONDecodeError:
            errors = [{"code": "INVALID_JSON", "message": proc.stdout[:500]}]
    ok = proc.returncode == 0 and status == "PASS"
    if not ok and not errors and proc.stderr.strip():
        errors = [{"code": "SUBPROCESS", "message": proc.stderr.strip()[:500]}]
    return DesignMachineGateResult(
        ok=ok,
        status=status,
        exit_code=proc.returncode,
        errors=errors,
    )


async def validate_apply_design_machine_async(
    change_id: str,
    *,
    repo_root: Path,
    timeout_sec: int = 120,
) -> DesignMachineGateResult:
    """Валидатор вне async hot path. Файл design не изменяет."""
    return await asyncio.to_thread(
        validate_apply_design_machine,
        change_id,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
    )


async def evaluate_apply_design_dependent_snapshot_async(
    change_id: str,
    *,
    repo_root: Path,
    timeout_sec: int = 60,
) -> DesignDependentSnapshotResult:
    """Снимок свежести вне async hot path."""
    return await asyncio.to_thread(
        evaluate_apply_design_dependent_snapshot,
        change_id,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
    )


_APPLY_DESIGN_READY_ACCEPTANCE_KEYS = (
    "write_boundary",
    "artifact_identity",
    "next_stage",
    "protected_context_identity",
    "reuse_decisions",
    "composition_completeness",
)


def apply_design_acceptance_field_gaps(body: Mapping[str, object]) -> list[str]:
    """Поля composition, обязательные для приёмки ``APPLY_DESIGN_READY``."""
    if str(body.get("status") or "") != "APPLY_DESIGN_READY":
        return []
    gaps: list[str] = []
    for key in _APPLY_DESIGN_READY_ACCEPTANCE_KEYS:
        if key not in body or body.get(key) is None:
            gaps.append(key)
    return gaps


def validate_apply_design_at_path(
    artifact_path: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 120,
) -> DesignMachineGateResult:
    """Запустить Node-валидатор для произвольного пути APPLY-DESIGN (staging)."""
    if not artifact_path.is_file():
        return DesignMachineGateResult(
            ok=False,
            status="MISSING",
            exit_code=2,
            errors=[{"code": "APPLY_DESIGN_MISSING", "message": "staging artifact missing"}],
        )
    rel = artifact_path.relative_to(repo_root).as_posix()
    script = resource_path(repo_root, "opsx_tools", "opsx-apply-design.mjs")
    proc = run_subprocess_utf8(
        ["node", str(script), "--repo", str(repo_root), rel],
        cwd=repo_root,
        timeout=timeout_sec,
    )
    errors: list[dict[str, object]] = []
    status = "UNKNOWN"
    if proc.stdout.strip():
        try:
            body = json.loads(proc.stdout)
            if isinstance(body, dict):
                status = str(body.get("status") or body.get("artifact_status") or status)
                raw_errors = body.get("errors")
                if isinstance(raw_errors, list):
                    errors = [e for e in raw_errors if isinstance(e, dict)]
        except json.JSONDecodeError:
            errors = [{"code": "INVALID_JSON", "message": proc.stdout[:500]}]
    ok = proc.returncode == 0 and status == "PASS"
    if not ok and not errors and proc.stderr.strip():
        errors = [{"code": "SUBPROCESS", "message": proc.stderr.strip()[:500]}]
    return DesignMachineGateResult(
        ok=ok,
        status=status,
        exit_code=proc.returncode,
        errors=errors,
    )


async def validate_apply_design_at_path_async(
    artifact_path: Path,
    *,
    repo_root: Path,
    timeout_sec: int = 120,
) -> DesignMachineGateResult:
    """Staging-валидатор вне async hot path."""
    return await asyncio.to_thread(
        validate_apply_design_at_path,
        artifact_path,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
    )


def evaluate_apply_design_snapshot_from_body(
    design: Mapping[str, object],
    *,
    repo_root: Path,
    timeout_sec: int = 60,
) -> DesignDependentSnapshotResult:
    """Сверка snapshot для кандидата до публикации в канон.

    Тело design сериализуется во временный файл: в argv передаётся только путь,
    чтобы не упереться в лимит командной строки Windows.
    """
    fd, temp_name = tempfile.mkstemp(suffix=".json", prefix="apply-design-snapshot-", dir=None)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(design, handle, ensure_ascii=False)
        return _run_apply_design_snapshot_signer(
            temp_path,
            repo_root=repo_root,
            timeout_sec=timeout_sec,
        )
    finally:
        temp_path.unlink(missing_ok=True)


async def evaluate_apply_design_snapshot_from_body_async(
    design: Mapping[str, object],
    *,
    repo_root: Path,
    timeout_sec: int = 60,
) -> DesignDependentSnapshotResult:
    """Снимок свежести кандидата вне async hot path."""
    return await asyncio.to_thread(
        evaluate_apply_design_snapshot_from_body,
        design,
        repo_root=repo_root,
        timeout_sec=timeout_sec,
    )


