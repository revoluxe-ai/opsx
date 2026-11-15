# -*- coding: utf-8 -*-
"""Authoritative implementation-completion projection for plan freeze (IMPL_OK)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from bounded_verify_plan import BoundedVerifyPlanError, change_dir

REPO_ROOT = Path(__file__).resolve().parents[3]

IMPL_NOT_READY = "IMPL_NOT_READY"
IMPL_COMPLETION_BLOCKED = "IMPL_COMPLETION_BLOCKED"
IMPL_COMPLETION_UNKNOWN = "IMPL_COMPLETION_UNKNOWN"

_HERMETIC_ENV = "BOUNDED_VERIFY_HERMETIC_IMPL"
_HERMETIC_AUTHORITY_REL = ".cursor/pipeline/bounded-verify-tests/fixtures/test-authority.token"
_EXPECTED_AUTHORITY_MARK = "bounded-verify-hermetic-test-authority-v1-do-not-use-in-production"


def hermetic_test_authority_granted(*, root: Path) -> bool:
    """Test-only boundary: env flag alone cannot authorize IMPL_OK."""
    if os.environ.get(_HERMETIC_ENV) != "1":
        return False
    if root.resolve() == REPO_ROOT.resolve():
        return False
    token_path = root / _HERMETIC_AUTHORITY_REL
    if not token_path.is_file():
        return False
    try:
        body = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return body == _EXPECTED_AUTHORITY_MARK


def _completion_script(root: Path) -> Path:
    return root / "opsx_tools" / "opsx-apply-verify-completion.mjs"


def _hermetic_projection_path(change_id: str, *, root: Path) -> Path:
    return (
        change_dir(change_id, root=root)
        / "bounded-verify"
        / "impl-completion-projection.json"
    )


def fetch_impl_completion_report(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Native split-APPLY completion classifier (or hermetic test projection)."""
    if hermetic_test_authority_granted(root=root):
        hermetic = _hermetic_projection_path(change_id, root=root)
        if hermetic.is_file():
            doc = json.loads(hermetic.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                authority = str(doc.get("test_authority_mark", ""))
                if authority == _EXPECTED_AUTHORITY_MARK:
                    return doc

    script = _completion_script(root)
    if not script.is_file():
        return {
            "split_apply": False,
            "legacy_apply_required": True,
            "marker": None,
            "status": "BLOCKED",
            "reason": "completion_runner_missing",
        }
    change_root = f"openspec/changes/{change_id}"
    proc = subprocess.run(
        ["node", str(script), "--change-root", change_root],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {
            "split_apply": False,
            "status": "BLOCKED",
            "reason": "completion_runner_failed",
            "stderr": proc.stderr.strip(),
        }
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"split_apply": False, "status": "BLOCKED", "reason": "completion_invalid_json"}
    if not isinstance(data, dict):
        return {"split_apply": False, "status": "BLOCKED", "reason": "completion_not_object"}
    return data


def implementation_completion_digest(change_id: str, *, root: Path = REPO_ROOT) -> str:
    """Stable digest bound to current implementation-completion evidence."""
    report = fetch_impl_completion_report(change_id, root=root)
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_implementation_ready_for_freeze(change_id: str, *, root: Path = REPO_ROOT) -> Mapping[str, Any]:
    """Fail-closed IMPL_OK gate before plan freeze."""
    report = fetch_impl_completion_report(change_id, root=root)
    if report.get("status") == "BLOCKED" and report.get("reason") == "completion_runner_failed":
        raise BoundedVerifyPlanError(
            IMPL_COMPLETION_UNKNOWN,
            "implementation completion projection unavailable",
        )
    if not report.get("split_apply"):
        if report.get("legacy_apply_required"):
            raise BoundedVerifyPlanError(
                IMPL_COMPLETION_BLOCKED,
                "split-APPLY completion required; legacy tasks.md checkbox is not authority",
            )
        raise BoundedVerifyPlanError(
            IMPL_COMPLETION_UNKNOWN,
            "implementation completion applicability unknown",
        )
    marker = str(report.get("marker") or "")
    blocking = report.get("blocking") or []
    if marker != "IMPL_OK" or blocking:
        raise BoundedVerifyPlanError(
            IMPL_NOT_READY,
            f"implementation not complete: marker={marker!r} blocking={len(blocking)}",
        )
    return report


def write_hermetic_impl_projection(
    change_id: str,
    *,
    root: Path,
    marker: str = "IMPL_OK",
) -> None:
    """Test-only durable IMPL_OK projection (requires BOUNDED_VERIFY_HERMETIC_IMPL=1)."""
    path = _hermetic_projection_path(change_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not hermetic_test_authority_granted(root=root):
        raise BoundedVerifyPlanError(
            IMPL_COMPLETION_BLOCKED,
            "hermetic IMPL projection refused without test authority token",
        )
    payload = {
        "split_apply": True,
        "marker": marker,
        "blocking": [],
        "task_projection": {
            "policy": "SPLIT_APPLY_CURRENT_GREEN",
            "apply_complete": True,
            "tasks": [],
        },
        "policy": "HERMETIC_TEST_PROJECTION",
        "test_authority_mark": _EXPECTED_AUTHORITY_MARK,
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
