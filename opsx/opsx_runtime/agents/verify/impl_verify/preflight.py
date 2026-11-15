"""Preflight и артефакты change для PHASE_IMPL (без LLM-вердикта)."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from agents.verify.implementation_track import count_implementation_track
from workflows.implementation.node_subprocess import run_subprocess_utf8

RUNTIME_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = project_root()
_LOGGER = logging.getLogger(__name__)


def change_root(change_id: str, *, repo_root: Path = REPO_ROOT) -> Path:
    """Каталог ``openspec/changes/<change_id>``."""
    return resource_path(repo_root, "openspec", "changes", change_id)


def _read_bounded(path: Path, *, max_chars: int | None = None) -> str | None:
    """Прочитать planning-артефакт целиком. ``max_chars`` не режет текст."""
    _ = max_chars
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_change_artifact_bundle(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Собрать текст planning-артефактов change для INPUT CONTEXT."""
    root = change_root(change_id, repo_root=repo_root)
    names = ("proposal.md", "design.md", "tasks.md", "owner-contract.md")
    bundle: dict[str, Any] = {
        "change_id": change_id,
        "change_root": str(root),
        "artifacts": {},
        "missing": [],
    }
    for name in names:
        path = resource_path(root, name)
        _LOGGER.info("Reading %s...", path)
        content = _read_bounded(path)
        if content is None:
            bundle["missing"].append(name)
        else:
            bundle["artifacts"][name] = content
    specs_dir = resource_path(root, "specs")
    bundle["spec_artifacts"] = {}
    if specs_dir.is_dir():
        spec_files = sorted(specs_dir.rglob("*.md"))
        bundle["spec_paths"] = [str(p.relative_to(repo_root)) for p in spec_files]
        for path in spec_files:
            content = _read_bounded(path)
            if content is not None:
                rel = str(path.relative_to(repo_root))
                bundle["spec_artifacts"][rel] = content
    else:
        bundle["spec_paths"] = []
    return bundle


_BACKTICK_PATH = re.compile(r"`([^`\n]+)`")
_SLASH_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])((?:src|tests|migrations)/[A-Za-z0-9_./\-]+)"
)


def derive_referenced_repo_paths(
    change_id: str,
    artifact_bundle: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    """Repo-relative пути из planning-артефактов change (scope для verify agents).

    Извлекает ``src/``, ``tests/``, ``migrations/`` и backtick-пути из текста
    proposal/design/tasks/owner-contract; добавляет ``spec_paths`` из bundle.
    Не ходит в FS — только парсинг уже загруженного ``artifact_bundle``.

    Args:
        change_id: Идентификатор change (для симметрии API; каталог change не сканируется).
        artifact_bundle: Результат ``load_change_artifact_bundle``.
        repo_root: Корень репозитория (зарезервировано для будущих FS-проверок).

    Returns:
        Отсортированный список уникальных repo-relative путей.
    """
    _ = change_id, repo_root
    seen: set[str] = set()
    ordered: list[str] = []

    def add_path(raw: str) -> None:
        token = raw.strip().strip(".,;:").replace("\\", "/")
        if not token or " " in token or token.startswith("http"):
            return
        if token.endswith("/"):
            token = token.rstrip("/")
        if len(token) < 4:
            return
        if token.startswith(f"openspec/changes/"):
            return
        if token not in seen:
            seen.add(token)
            ordered.append(token)

    for rel in artifact_bundle.get("spec_paths") or []:
        token = str(rel).strip().replace("\\", "/")
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)

    artifacts = artifact_bundle.get("artifacts")
    if not isinstance(artifacts, dict):
        return sorted(ordered)
    blob = "\n".join(str(v) for v in artifacts.values() if isinstance(v, str))
    for match in _BACKTICK_PATH.finditer(blob):
        candidate = match.group(1)
        if "/" in candidate:
            add_path(candidate)
    for match in _SLASH_PATH.finditer(blob):
        add_path(match.group(1))

    return sorted(ordered)


def run_config_contract_check(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """``python openspec/tools/check_config_contract.py`` — gate для других hops (не impl verdict)."""
    script = resource_path(repo_root, "openspec", "tools", "check_config_contract.py")
    if not script.is_file():
        return {"status": "SKIP", "reason": "check_config_contract.py missing"}
    proc = run_subprocess_utf8(
        [sys.executable, str(script)],
        cwd=repo_root,
    )
    return {
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:] if proc.stdout else "",
        "stderr": proc.stderr[-2000:] if proc.stderr else "",
    }


_GAP_STALE_EVIDENCE = "STALE_EVIDENCE"
_GAP_BUREAUCRACY = "BUREAUCRACY"
_STALE_GAP_CLASSES = frozenset({_GAP_STALE_EVIDENCE, "STALE_GREEN"})
_NON_BLOCKING_GAP_CLASSES = frozenset(
    {
        _GAP_STALE_EVIDENCE,
        "STALE_GREEN",
        _GAP_BUREAUCRACY,
        "TEST_COVERAGE_ARTIFACT_GAP",
        "PROCESS_COMPLETION_PROJECTION_GAP",
    }
)


def _first_slice_hint(gaps: list[dict[str, Any]]) -> str | None:
    """Первый slice_id / covering slice для action-hint (обычно SLICE-001)."""
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        sid = gap.get("slice_id")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
        covering = gap.get("covering_slice_ids")
        if isinstance(covering, list):
            for item in covering:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return None


def collapse_stale_evidence_gaps(
    gaps: list[Any],
    *,
    change_id: str,
    next_action: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Свернуть per-task STALE_* gaps в одну WARN-запись (не blocking).

    Returns:
        (optimized_gaps, stale_only) — stale_only для метаданных collapsed_count.
    """
    _ = change_id, next_action
    stale_only: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for raw in gaps or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("class") in _STALE_GAP_CLASSES:
            stale_only.append(raw)
        else:
            other.append(raw)
    if len(stale_only) <= 1:
        return [dict(g) for g in stale_only + other], stale_only
    slice_hint = _first_slice_hint(stale_only)
    slices: set[str] = set()
    for gap in stale_only:
        sid = gap.get("slice_id")
        if isinstance(sid, str) and sid:
            slices.add(sid)
        for cid in gap.get("covering_slice_ids") or []:
            if isinstance(cid, str) and cid:
                slices.add(cid)
    collapsed: dict[str, Any] = {
        "class": _GAP_STALE_EVIDENCE,
        "collapsed": True,
        "collapsed_count": len(stale_only),
        "covering_slice_ids": sorted(slices),
        "severity": "WARN",
        "detail": (
            f"{len(stale_only)} tasks: CURRENT_GREEN stale "
            f"(STALE_GREEN on {', '.join(sorted(slices)) or 'slices'})"
        ),
        "note": (
            "impl_verify auto-runs bounded pytest for stale slices; "
            "stale stamp alone does not block IMPL_OK"
        ),
    }
    if slice_hint:
        collapsed["slice_id"] = slice_hint
    return [collapsed, *other], stale_only


def _gap_is_stale(gap: dict[str, Any]) -> bool:
    return str(gap.get("class") or "") in _STALE_GAP_CLASSES


def _gap_is_non_blocking(gap: dict[str, Any]) -> bool:
    """Gaps, которые не должны переводить machine-marker в IMPL_GAPS."""
    return str(gap.get("class") or "") in _NON_BLOCKING_GAP_CLASSES


def scan_scope_test_presence(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Проверить наличие test-файлов из scope planning-артефактов (без pytest).

    Отсутствие файла — WARN для receipt; не блокирует IMPL_OK вердикт LLM.
    """
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    referenced = derive_referenced_repo_paths(change_id, bundle, repo_root=repo_root)
    test_paths = [
        rel
        for rel in referenced
        if rel.startswith("tests/") or "/test_" in rel or rel.endswith("_test.py")
    ]
    present: list[str] = []
    missing: list[str] = []
    for rel in test_paths:
        candidate = resource_path(repo_root, rel.replace("\\", "/"))
        if candidate.is_file():
            present.append(rel)
        else:
            missing.append(rel)
    warnings: list[dict[str, Any]] = []
    if missing:
        warnings.append(
            {
                "severity": "WARN",
                "class": "MISSING_SCOPE_TEST_FILE",
                "detail": f"missing referenced test files: {', '.join(missing)}",
                "paths": missing,
            }
        )
    return {
        "referenced_test_paths": test_paths,
        "present": present,
        "missing": missing,
        "warnings": warnings,
    }


def optimize_split_apply_completion_payload(
    payload: dict[str, Any],
    *,
    change_id: str,
) -> dict[str, Any]:
    """Сжать gaps для receipt/report: STALE/BUREAUCRACY → warnings, не blocking.

    Не меняет ``task_projection`` — только ``gaps``, ``blocking``, ``marker``, ``warnings``.
    """
    if not payload or payload.get("status") in ("BLOCKED", "SKIP"):
        return payload
    gaps_in = payload.get("gaps")
    if not isinstance(gaps_in, list):
        return payload
    bureaucracy: list[dict[str, Any]] = []
    stale_raw: list[dict[str, Any]] = []
    soft_gaps: list[dict[str, Any]] = []
    product_gaps: list[dict[str, Any]] = []
    for raw in gaps_in:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get("class") == _GAP_BUREAUCRACY:
            bureaucracy.append(item)
        elif _gap_is_stale(item):
            stale_raw.append(item)
        elif _gap_is_non_blocking(item):
            soft_gaps.append(item)
        else:
            product_gaps.append(item)
    next_action = payload.get("next_action")
    if not isinstance(next_action, dict):
        next_action = None
    stale_warnings, _stale_src = collapse_stale_evidence_gaps(
        stale_raw,
        change_id=change_id,
        next_action=next_action,
    )
    optimized_gaps = list(product_gaps)
    if len(stale_raw) == 1:
        optimized_gaps = [dict(stale_raw[0]), *optimized_gaps]
    elif stale_warnings:
        optimized_gaps = [*stale_warnings, *optimized_gaps]

    blocking_in = payload.get("blocking")
    if isinstance(blocking_in, list):
        blocking = [
            dict(g)
            for g in blocking_in
            if isinstance(g, dict) and not _gap_is_non_blocking(g)
        ]
    else:
        blocking = [
            g
            for g in product_gaps
            if isinstance(g, dict)
            and g.get("class") not in ("PROCESS_COMPLETION_PROJECTION_GAP",)
        ]
    warnings: list[dict[str, Any]] = list(payload.get("warnings") or [])
    for warn in bureaucracy + soft_gaps:
        entry = dict(warn)
        entry.setdefault("severity", "WARN")
        warnings.append(entry)
    for warn in stale_warnings:
        entry = dict(warn)
        entry.setdefault("severity", "WARN")
        warnings.append(entry)
    marker = "IMPL_GAPS" if blocking else "IMPL_OK"
    out = dict(payload)
    out["gaps"] = optimized_gaps
    out["blocking"] = blocking
    out["marker"] = marker
    if warnings:
        out["warnings"] = warnings
    out["gap_optimization"] = {
        "stale_collapsed": len(_stale_src) > 1,
        "stale_source_count": len(_stale_src),
        "bureaucracy_warn_count": len(bureaucracy),
        "stale_warn_only": bool(_stale_src),
    }
    return out


def preflight_implies_gaps(preflight: dict[str, Any]) -> list[str]:
    """Blocking machine-gap строки для routing/owner_attention (не override IMPL_OK LLM).

    STALE/BUREAUCRACY/soft projection gaps сюда не входят — только продуктовые
    ``split_apply_completion.blocking`` и open IMPLEMENTATION_TRACK checkboxes.
    """
    lines: list[str] = []
    track = preflight.get("implementation_track") or {}
    track_remaining = int(track.get("remaining") or 0)
    if track_remaining > 0:
        lines.append(
            f"IMPLEMENTATION_TRACK_INCOMPLETE: {track_remaining} open apply-track checkbox(es)"
        )
    split = preflight.get("split_apply_completion")
    if not isinstance(split, dict):
        return lines
    blocking = split.get("blocking")
    if not isinstance(blocking, list):
        return lines
    for gap in blocking:
        if not isinstance(gap, dict) or _gap_is_non_blocking(gap):
            continue
        detail = str(gap.get("detail") or gap.get("class") or "blocking gap").strip()
        cls = str(gap.get("class") or "").strip()
        lines.append(f"{cls}: {detail}" if cls else detail)
    return lines


def run_impl_verify_stale_green_refresh(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """При STALE_GREEN запустить bounded pytest и обновить GREEN receipts (PHASE_IMPL)."""
    script = resource_path(repo_root, "opsx_tools", "opsx-impl-verify-refresh-stale-green.mjs")
    if not script.is_file():
        return {"status": "SKIP", "reason": "refresh_runner_missing"}
    change_root_rel = f"openspec/changes/{change_id}"
    proc = run_subprocess_utf8(
        ["node", str(script), "--change-root", change_root_rel],
        cwd=repo_root,
        timeout=7200,
    )
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {
            "status": "BLOCKED",
            "reason": "refresh_runner_empty",
            "exit_code": proc.returncode,
            "stderr": (proc.stderr or "")[-2000:],
        }
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "status": "BLOCKED",
            "reason": "refresh_invalid_json",
            "exit_code": proc.returncode,
            "stdout_tail": stdout[-2000:],
        }
    if not isinstance(data, dict):
        return {"status": "BLOCKED", "reason": "refresh_not_object"}
    data.setdefault("exit_code", proc.returncode)
    return data


def run_split_apply_completion(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Классификатор split-APPLY (``opsx-apply-verify-completion.mjs``) — контекст receipt."""
    script = resource_path(repo_root, "opsx_tools", "opsx-apply-verify-completion.mjs")
    if not script.is_file():
        return {"status": "BLOCKED", "reason": "completion_runner_missing"}
    change_root_rel = f"openspec/changes/{change_id}"
    proc = run_subprocess_utf8(
        ["node", str(script), "--change-root", change_root_rel],
        cwd=repo_root,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {
            "status": "BLOCKED",
            "reason": "completion_runner_failed",
            "stderr": proc.stderr.strip(),
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "BLOCKED", "reason": "completion_invalid_json"}
    if not isinstance(data, dict):
        return {"status": "BLOCKED", "reason": "completion_not_object"}
    return optimize_split_apply_completion_payload(data, change_id=change_id)


def collect_deterministic_preflight(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Machine-контекст для receipt (read-only; не override verdict LLM)."""
    root = change_root(change_id, repo_root=repo_root)
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    tasks_blob = (bundle.get("artifacts") or {}).get("tasks.md")
    implementation_track = count_implementation_track(
        tasks_blob if isinstance(tasks_blob, str) else None
    )
    scope_tests = scan_scope_test_presence(change_id, repo_root=repo_root)
    preflight_warnings = list(scope_tests.get("warnings") or [])
    stale_refresh = run_impl_verify_stale_green_refresh(change_id, repo_root=repo_root)
    split_apply = run_split_apply_completion(change_id, repo_root=repo_root)
    split_warnings = split_apply.get("warnings") if isinstance(split_apply, dict) else None
    if isinstance(split_warnings, list):
        preflight_warnings.extend(split_warnings)
    runner_missing = (
        isinstance(split_apply, dict)
        and split_apply.get("status") == "BLOCKED"
        and split_apply.get("reason") == "completion_runner_missing"
    )
    apply_completion_status = (
        "NOT_EVALUATED"
        if runner_missing
        else (split_apply.get("marker") if isinstance(split_apply, dict) else "NOT_EVALUATED")
    )
    out: dict[str, Any] = {
        "change_id": change_id,
        "change_exists": root.is_dir(),
        "artifact_bundle_meta": {
            "missing": bundle["missing"],
            "spec_paths": bundle.get("spec_paths") or [],
        },
        "implementation_track": implementation_track,
        "config_contract": run_config_contract_check(repo_root=repo_root),
        "scope_test_presence": scope_tests,
        "stale_green_refresh": stale_refresh,
        "apply_completion_status": apply_completion_status,
        "warnings": preflight_warnings or None,
    }
    if not runner_missing:
        out["split_apply_completion"] = split_apply
    return out


def collect_apply_machine_context(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Machine-контекст для apply-hop (классификатор split-apply; не для impl_verify verdict)."""
    return {
        "change_id": change_id,
        "config_contract": run_config_contract_check(repo_root=repo_root),
        "split_apply_completion": run_split_apply_completion(
            change_id,
            repo_root=repo_root,
        ),
    }
