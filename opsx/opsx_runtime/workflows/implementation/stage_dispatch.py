"""Stage selection SoT: subprocess ``opsx-super-implement.mjs plan``."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_PLAN_SUBPROCESS_TIMEOUT_SEC = 120

from workflows.implementation.design_pipeline.routing import DESIGN_LANE_STAGES
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.implementation.routing_contract import (
    RUN_STATUS_RUNNING,
    SuperImplementPlanStep,
    format_current_implementation_procedure,
    parse_super_implement_plan_payload,
)
from workflows.repo_paths import resolve_opsx_repository_root


def _repo_relative_change_root(change_id: str, *, repo_root: Path) -> str:
    return f"openspec/changes/{change_id}"


def _super_implement_script(repo_root: Path) -> Path:
    return resource_path(repo_root, "opsx_tools", "opsx-super-implement.mjs")


def advance_super_implement_receipt(
    change_id: str,
    *,
    repo_root: Path,
    live_execution_authorized: bool = False,
) -> dict[str, Any]:
    """``advance`` — записать/зафиксировать ``RUN.json`` receipt (Node SoT)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    script = _super_implement_script(repo_root)
    if not script.is_file():
        msg = f"super-implement script not found: {script}"
        raise RuntimeError(msg)

    rel_root = _repo_relative_change_root(change_id, repo_root=repo_root)
    live_flag = "true" if live_execution_authorized else "false"
    cmd = [
        "node",
        str(script),
        "advance",
        "--change-root",
        rel_root,
        "--live-execution",
        live_flag,
    ]
    proc = run_subprocess_utf8(cmd, cwd=repo_root)
    if proc.returncode not in (0, 1):
        msg = (
            f"opsx-super-implement advance failed exit={proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
        raise RuntimeError(msg)
    stdout = proc.stdout.strip()
    if not stdout:
        msg = "opsx-super-implement advance returned empty stdout"
        raise RuntimeError(msg)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"opsx-super-implement advance invalid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = "opsx-super-implement advance JSON root must be object"
        raise RuntimeError(msg)
    return payload


def consume_plan_cached_after_hop(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Вернуть plan JSON из state, если hop stagnation уже вызвал plan в этом переходе."""
    if not state.get("implementation_plan_reuse_after_hop"):
        return None
    raw = state.get("implementation_plan")
    if isinstance(raw, dict):
        return raw
    return None


def consume_plan_from_completed_design_lane(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Не вызывать Node plan повторно, если design subgraph уже спланировал forward stage."""
    design_lane_complete = bool(state.get("apply_design_lane_complete"))
    status = str(state.get("implementation_status") or "")
    stage = str(state.get("implementation_stage") or "").strip()
    design_lane_handoff = design_lane_complete and status == "IN_PROGRESS" and not stage
    parent_forward_without_flag = (
        status == "IN_PROGRESS" and not stage and not design_lane_complete
    )
    if not design_lane_handoff and not parent_forward_without_flag:
        return None
    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return None
    step = parse_super_implement_plan_payload(raw)
    next_stage = str(step.next_stage or "").strip()
    if step.status != RUN_STATUS_RUNNING or not next_stage:
        return None
    if next_stage in DESIGN_LANE_STAGES:
        return None
    return raw


def resolve_super_implement_plan_step(
    change_id: str,
    *,
    state: Mapping[str, Any],
    repo_root: Path,
    live_execution_authorized: bool = False,
    plan_payload_override: dict[str, Any] | None = None,
) -> tuple[SuperImplementPlanStep, bool]:
    """Plan subprocess или reuse свежего ``implementation_plan`` без повторного вызова."""
    cached = consume_plan_cached_after_hop(state)
    if cached is not None:
        return parse_super_implement_plan_payload(cached), True
    design_lane_plan = consume_plan_from_completed_design_lane(state)
    if design_lane_plan is not None:
        design_lane_complete = bool(state.get("apply_design_lane_complete"))
        procedure = format_current_implementation_procedure(
            str(design_lane_plan.get("next_stage") or "").strip() or None,
            str(design_lane_plan.get("next_slice") or "").strip() or None,
        )
        logger.info(
            "super-implement plan reuse from design_lane change_id=%s procedure=%s "
            "apply_design_lane_complete=%s",
            change_id,
            procedure,
            design_lane_complete,
        )
        return parse_super_implement_plan_payload(design_lane_plan), True
    step = plan_super_implement_step(
        change_id,
        repo_root=repo_root,
        live_execution_authorized=live_execution_authorized,
        plan_payload_override=plan_payload_override,
    )
    return step, False


def plan_super_implement_step(
    change_id: str,
    *,
    repo_root: Path,
    live_execution_authorized: bool = False,
    plan_payload_override: dict[str, Any] | None = None,
) -> SuperImplementPlanStep:
    """Вызвать Node plan или использовать override (tests).

    Args:
        change_id: OpenSpec change id.
        repo_root: Корень репозитория.
        live_execution_authorized: Проброс в ``--live-execution``.
        plan_payload_override: Если задан — не вызывать subprocess.

    Returns:
        Нормализованный plan step.

    Raises:
        RuntimeError: subprocess / JSON failure.
    """
    if plan_payload_override is not None:
        return parse_super_implement_plan_payload(plan_payload_override)

    repo_root = resolve_opsx_repository_root(repo_root)
    script = _super_implement_script(repo_root)
    if not script.is_file():
        msg = f"super-implement script not found: {script}"
        raise RuntimeError(msg)

    rel_root = _repo_relative_change_root(change_id, repo_root=repo_root)
    live_flag = "true" if live_execution_authorized else "false"
    cmd = [
        "node",
        str(script),
        "plan",
        "--change-root",
        rel_root,
        "--live-execution",
        live_flag,
    ]
    started = time.monotonic()
    logger.info(
        "super-implement plan begin change_id=%s change_root=%s",
        change_id,
        rel_root,
    )
    try:
        proc = run_subprocess_utf8(
            cmd,
            cwd=repo_root,
            timeout=_PLAN_SUBPROCESS_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "super-implement plan timeout change_id=%s elapsed_ms=%s",
            change_id,
            elapsed_ms,
        )
        msg = (
            f"opsx-super-implement plan timed out after {_PLAN_SUBPROCESS_TIMEOUT_SEC}s "
            f"(change_id={change_id})"
        )
        raise RuntimeError(msg) from exc
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "super-implement plan end change_id=%s exit=%s elapsed_ms=%s",
        change_id,
        proc.returncode,
        elapsed_ms,
    )
    if proc.returncode not in (0, 1):
        msg = (
            f"opsx-super-implement plan failed exit={proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
        raise RuntimeError(msg)
    stdout = proc.stdout.strip()
    if not stdout:
        msg = "opsx-super-implement plan returned empty stdout"
        raise RuntimeError(msg)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = f"opsx-super-implement plan invalid JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = "opsx-super-implement plan JSON root must be object"
        raise RuntimeError(msg)
    return parse_super_implement_plan_payload(payload)


def node_binary_hint() -> str:
    """Диагностика: какой node используется."""
    return sys.executable
