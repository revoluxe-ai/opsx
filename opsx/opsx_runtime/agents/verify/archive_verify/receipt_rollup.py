"""Детерминированный rollup verify-receipts для PHASE_ARCHIVE (read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.verify.impl_verify.preflight import REPO_ROOT
from workflows.artifact_paths import impl_verify_receipt_path, resolve_impl_verify_receipt_path


def collect_verify_receipt_rollup(
    change_id: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Проверить наличие и вердикт ``IMPL-VERIFY-RECEIPT.json``.

    Primary archive machine path: без текущего ``IMPL_OK`` receipt — NO_GO hint.
    """
    impl_path = resolve_impl_verify_receipt_path(repo_root, change_id) or impl_verify_receipt_path(
        repo_root,
        change_id,
    )
    gaps: list[str] = []
    impl_receipt: dict[str, Any] | None = None
    if not impl_path.is_file():
        gaps.append("VERIFY_RECEIPT_MISSING: IMPL-VERIFY-RECEIPT.json")
        return {"impl_receipt": None, "receipt_gaps": gaps, "status": "FAIL"}
    try:
        raw = json.loads(impl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        gaps.append("VERIFY_RECEIPT_INVALID: IMPL-VERIFY-RECEIPT.json")
        return {"impl_receipt": None, "receipt_gaps": gaps, "status": "FAIL"}
    if not isinstance(raw, dict):
        gaps.append("VERIFY_RECEIPT_INVALID: IMPL-VERIFY-RECEIPT.json not object")
        return {"impl_receipt": None, "receipt_gaps": gaps, "status": "FAIL"}
    verdict = str(raw.get("verdict") or "").strip()
    impl_receipt = {
        "path": str(impl_path.relative_to(repo_root)),
        "verdict": verdict,
        "marker": raw.get("marker"),
        "mode": raw.get("mode"),
    }
    if verdict != "IMPL_OK":
        gaps.append(f"VERIFY_RECEIPT_IMPL_NOT_OK: {verdict or 'empty'}")
    status = "PASS" if not gaps else "FAIL"
    return {"impl_receipt": impl_receipt, "receipt_gaps": gaps, "status": status}
