"""Детерминированная доводка RED receipt после hop (identity + live executions)."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from dataclasses import dataclass
from pathlib import Path

from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repo_paths import resolve_opsx_repository_root


@dataclass(frozen=True, slots=True)
class RedReceiptShapeResult:
    """Исход shape CLI для slice RED."""

    ok: bool
    code: str | None
    test_content_identity: str | None


def shape_red_receipt_after_hop(
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
    timeout_sec: int = 300,
) -> RedReceiptShapeResult:
    """Вызвать ``opsx-shape-slice-red-receipt-cli.mjs`` для authoritative stamps."""
    repo_root = resolve_opsx_repository_root(repo_root)
    script = resource_path(repo_root, "opsx_tools", "opsx-shape-slice-red-receipt-cli.mjs")
    if not script.is_file():
        return RedReceiptShapeResult(False, "SHAPE_CLI_MISSING", None)
    run = run_subprocess_utf8(
        [
            "node",
            str(script),
            "--repo",
            str(repo_root),
            "--change",
            change_id,
            "--slice",
            slice_id,
        ],
        timeout=timeout_sec,
    )
    if run.returncode != 0 or not run.stdout.strip():
        return RedReceiptShapeResult(False, "SHAPE_CLI_FAILED", None)
    try:
        body = json.loads(run.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        return RedReceiptShapeResult(False, "SHAPE_CLI_BAD_JSON", None)
    if body.get("ok") is not True:
        return RedReceiptShapeResult(
            False,
            str(body.get("code") or "SHAPE_REJECTED"),
            None,
        )
    tid = body.get("test_content_identity")
    test_id = tid.strip() if isinstance(tid, str) and tid.strip() else None
    return RedReceiptShapeResult(True, None, test_id)
