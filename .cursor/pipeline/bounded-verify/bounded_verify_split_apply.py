# -*- coding: utf-8 -*-
"""Split-APPLY completion identity для snapshot v2 (native GREEN carriers)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_plan import BoundedVerifyPlanError, change_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

SPLIT_APPLY_INCOMPLETE = "SPLIT_APPLY_INCOMPLETE"
SPLIT_APPLY_UNRESOLVED = "SPLIT_APPLY_UNRESOLVED"
SPLIT_APPLY_STALE = "SPLIT_APPLY_STALE"
SPLIT_APPLY_NATIVE_BLOCKED = "SPLIT_APPLY_NATIVE_BLOCKED"

GREEN_PROVEN = "GREEN_PROVEN"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _not_applicable_digest() -> str:
    return _sha256_bytes(b"NOT_APPLICABLE")


def _slice_ids_from_design(design: Mapping[str, Any]) -> list[str]:
    order = design.get("slice_order")
    if isinstance(order, dict) and isinstance(order.get("nodes"), list):
        return [str(x) for x in order["nodes"]]
    slices = design.get("slices")
    if isinstance(slices, list):
        return [str(s.get("slice_id")) for s in slices if isinstance(s, dict) and s.get("slice_id")]
    return []


def _split_apply_design_path(change_id: str, *, root: Path) -> Path:
    return change_dir(change_id, root=root) / "apply-design" / "APPLY-DESIGN.json"


def split_apply_applicable(change_id: str, *, root: Path = REPO_ROOT) -> bool:
    """True when change carries split-APPLY design authority."""
    return _split_apply_design_path(change_id, root=root).is_file()


def _require_native_freshness(change_id: str, *, root: Path) -> dict[str, Any]:
    """Mandatory native freshness for split-APPLY changes (no Python fallback)."""
    script = root / "opsx_tools" / "opsx-bounded-verify-green-freshness.mjs"
    if not script.is_file():
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native GREEN freshness runner missing",
        )
    change_root = f"openspec/changes/{change_id}"
    proc = subprocess.run(
        ["node", str(script), "--change-root", change_root],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native GREEN freshness failed or empty output",
        )
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native GREEN freshness returned invalid JSON",
        ) from exc
    if report.get("ok") is not True:
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            f"native freshness not ok: {report.get('code')}",
        )
    if not report.get("split_apply"):
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_UNRESOLVED,
            "APPLY-DESIGN present but native reports split_apply=false",
        )
    expected_slices = _slice_ids_from_design(
        json.loads(_split_apply_design_path(change_id, root=root).read_text(encoding="utf-8"))
    )
    reported = [str(row.get("slice_id", "")) for row in report.get("slices", [])]
    if set(reported) != set(expected_slices) or len(reported) != len(expected_slices):
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native freshness slice set mismatch vs APPLY-DESIGN",
        )
    stale_rows = [
        row
        for row in report.get("slices", [])
        if (row.get("freshness") or {}).get("current") is not True
    ]
    if stale_rows:
        adapter = root / "opsx_tools" / "opsx-break-glass-process-gate-deferral.mjs"
        if not adapter.is_file():
            row = stale_rows[0]
            fr = row.get("freshness") or {}
            raise BoundedVerifyPlanError(
                SPLIT_APPLY_STALE,
                f"slice {row.get('slice_id')}: {fr.get('code')} ({fr.get('mismatched')})",
            )
        change_root = f"openspec/changes/{change_id}"
        proc = subprocess.run(
            [
                "node",
                str(adapter),
                "split-apply-freshness",
                "--change-root",
                change_root,
                "--change-id",
                change_id,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            row = stale_rows[0]
            fr = row.get("freshness") or {}
            raise BoundedVerifyPlanError(
                SPLIT_APPLY_STALE,
                f"slice {row.get('slice_id')}: {fr.get('code')} ({fr.get('mismatched')})",
            )
        try:
            route = json.loads(proc.stdout)
        except json.JSONDecodeError:
            row = stale_rows[0]
            fr = row.get("freshness") or {}
            raise BoundedVerifyPlanError(
                SPLIT_APPLY_STALE,
                f"slice {row.get('slice_id')}: {fr.get('code')} ({fr.get('mismatched')})",
            ) from None
        if route.get("ok") is not True or route.get("exception_route") is not True:
            row = stale_rows[0]
            fr = row.get("freshness") or {}
            raise BoundedVerifyPlanError(
                SPLIT_APPLY_STALE,
                f"slice {row.get('slice_id')}: {fr.get('code')} ({fr.get('mismatched')})",
            )
        report = {
            **report,
            "exception_route": True,
            "gate_check_status": "OWNER_OVERRIDDEN",
            "ordinary_green_freshness_debt": route.get("ordinary_green_freshness_debt"),
            "process_gate_deferral_path": route.get("deferral_path"),
        }
    return report


def _require_native_completion_projection(change_id: str, *, root: Path) -> dict[str, Any]:
    script = root / "opsx_tools" / "opsx-apply-verify-completion.mjs"
    if not script.is_file():
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native split-apply completion runner missing",
        )
    change_root = f"openspec/changes/{change_id}"
    proc = subprocess.run(
        ["node", str(script), "--change-root", change_root],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native completion projection failed or empty output",
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_NATIVE_BLOCKED,
            "native completion projection invalid JSON",
        ) from exc
    if not data.get("split_apply"):
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_UNRESOLVED,
            "native completion reports split_apply=false for split-APPLY change",
        )
    blocking = data.get("blocking") or []
    if blocking:
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_INCOMPLETE,
            f"split-apply blocking gaps: {blocking}",
        )
    projection = data.get("task_projection") or {}
    canonical = json.dumps(
        {"policy": "SPLIT_APPLY_CURRENT_GREEN", "task_projection": projection},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "split_apply": True,
        "policy": "SPLIT_APPLY_CURRENT_GREEN",
        "digest": _sha256_bytes(canonical),
        "task_projection": projection,
    }


def _node_split_apply_projection_optional(change_id: str, *, root: Path) -> dict[str, Any] | None:
    """Non-split-apply changes: optional native NOT_APPLICABLE probe."""
    script = root / "opsx_tools" / "opsx-apply-verify-completion.mjs"
    if not script.is_file():
        return None
    change_root = f"openspec/changes/{change_id}"
    proc = subprocess.run(
        ["node", str(script), "--change-root", change_root],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not data.get("split_apply"):
        return {"split_apply": False}
    blocking = data.get("blocking") or []
    if blocking:
        raise BoundedVerifyPlanError(
            SPLIT_APPLY_INCOMPLETE,
            f"split-apply blocking gaps: {blocking}",
        )
    projection = data.get("task_projection") or {}
    canonical = json.dumps(
        {"policy": "SPLIT_APPLY_CURRENT_GREEN", "task_projection": projection},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "split_apply": True,
        "policy": "SPLIT_APPLY_CURRENT_GREEN",
        "digest": _sha256_bytes(canonical),
        "task_projection": projection,
    }


def split_apply_green_identity(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Returns apply_green digest + policy; fail-closed when split-apply unresolved."""
    if split_apply_applicable(change_id, root=root):
        _require_native_freshness(change_id, root=root)
        node_view = _require_native_completion_projection(change_id, root=root)
        return {
            "policy": node_view["policy"],
            "digest": node_view["digest"],
            "source": "opsx-apply-verify-completion.mjs",
        }

    node_view = _node_split_apply_projection_optional(change_id, root=root)
    if node_view is not None:
        if not node_view.get("split_apply"):
            return {"policy": "NOT_APPLICABLE", "digest": _not_applicable_digest()}
        return {
            "policy": node_view["policy"],
            "digest": node_view["digest"],
            "source": "opsx-apply-verify-completion.mjs",
        }

    return {"policy": "NOT_APPLICABLE", "digest": _not_applicable_digest()}
