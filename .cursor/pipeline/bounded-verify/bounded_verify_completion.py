# -*- coding: utf-8 -*-
"""Split-APPLY completion identity и freshness signals для bounded-verify plan.

Связывает ``opsx-apply-verify-completion.mjs`` и GREEN freshness из
``opsx_tools/opsx-apply-pipeline-identity.mjs`` без дублирования семантики
в Python.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_atomic import canonical_json_bytes
from bounded_verify_plan import (
    CHANGE_NOT_FOUND,
    TASKS_INCOMPLETE,
    BoundedVerifyPlanError,
    change_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

COMPLETION_POLICY_SPLIT = "SPLIT_APPLY_CURRENT_GREEN"
COMPLETION_POLICY_LEGACY = "LEGACY_TASKS_MD"


def _run_node_json(script_rel: str, args: list[str], *, root: Path) -> dict[str, Any]:
    """Запустить node-скрипт и распарсить JSON из stdout."""
    script = root / script_rel
    if not script.is_file():
        raise BoundedVerifyPlanError(
            "COMPLETION_TOOL_MISSING",
            f"missing script: {script_rel}",
        )
    completed = subprocess.run(
        ["node", str(script), *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[:500]
        raise BoundedVerifyPlanError(
            "COMPLETION_TOOL_ERROR",
            f"node {script_rel} exit {completed.returncode}: {tail}",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BoundedVerifyPlanError(
            "COMPLETION_TOOL_ERROR",
            f"invalid JSON from {script_rel}: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise BoundedVerifyPlanError("COMPLETION_TOOL_ERROR", "completion JSON not object")
    return payload


def split_apply_completion_payload(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any] | None:
    """Вернуть split-apply completion JSON или None если change не split-APPLY."""
    cdir = change_dir(change_id, root=root)
    if not (cdir / "apply-design" / "APPLY-DESIGN.json").is_file():
        return None
    rel = f"openspec/changes/{change_id}"
    return _run_node_json(
        "opsx_tools/opsx-apply-verify-completion.mjs",
        ["--change-root", rel],
        root=root,
    )


def stale_green_slice_ids(change_id: str, *, root: Path = REPO_ROOT) -> list[str]:
    """Список slice_id с ``classifyGreenEvidenceFreshness``.current=false."""
    rel = f"openspec/changes/{change_id}"
    payload = _run_node_json(
        "opsx_tools/bounded-verify-list-stale-green.mjs",
        [rel],
        root=root,
    )
    stale_rows = payload.get("stale", [])
    if not isinstance(stale_rows, list):
        return []
    return [str(row.get("slice_id", row)) for row in stale_rows if row]


def build_implementation_snapshot(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, str]:
    """Собрать ``implementation_snapshot`` для plan.json (fingerprinted subset)."""
    cdir = change_dir(change_id, root=root)
    if not cdir.is_dir():
        raise BoundedVerifyPlanError(CHANGE_NOT_FOUND, f"change not found: {change_id}")

    split_payload = split_apply_completion_payload(change_id, root=root)
    if split_payload and split_payload.get("split_apply"):
        projection = split_payload.get("task_projection") or {}
        marker = str(split_payload.get("marker") or "IMPL_GAPS")
        digest_body = {
            "completion_policy": COMPLETION_POLICY_SPLIT,
            "change_id": change_id,
            "split_apply_marker": marker,
            "apply_complete": bool(projection.get("apply_complete")),
            "task_projection_policy": projection.get("policy"),
        }
        digest = hashlib.sha256(canonical_json_bytes(digest_body)).hexdigest()
        return {
            "content_digest": digest,
            "completion_policy": COMPLETION_POLICY_SPLIT,
            "split_apply_marker": marker,
        }

    tasks_path = cdir / "tasks.md"
    if not tasks_path.is_file():
        raise BoundedVerifyPlanError(TASKS_INCOMPLETE, "tasks.md missing")
    content = tasks_path.read_text(encoding="utf-8")
    if "[x]" not in content.lower():
        raise BoundedVerifyPlanError(TASKS_INCOMPLETE, "tasks.md has no complete items")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "content_digest": digest,
        "completion_policy": COMPLETION_POLICY_LEGACY,
    }


def assert_impl_ready_for_plan(change_id: str, *, root: Path = REPO_ROOT) -> None:
    """Fail-closed: split-APPLY marker IMPL_OK или legacy tasks complete."""
    split_payload = split_apply_completion_payload(change_id, root=root)
    if split_payload and split_payload.get("split_apply"):
        marker = str(split_payload.get("marker") or "IMPL_GAPS")
        if marker != "IMPL_OK":
            raise BoundedVerifyPlanError(
                TASKS_INCOMPLETE,
                f"split-apply impl not ready: marker={marker}",
            )
        return
    _ = build_implementation_snapshot(change_id, root=root)


def channel_impact_applicable(change_id: str, *, root: Path = REPO_ROOT) -> bool:
    """True если change объявлен APPLICABLE в channel-impact-guard.json."""
    path = change_dir(change_id, root=root) / "channel-impact-guard.json"
    if not path.is_file():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("applicability", "")).upper() == "APPLICABLE"
