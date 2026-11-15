"""S5 adapter runner для obligation incremental-quality-coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from iqc_quality_audit import audit_incremental_quality_coverage


def run_incremental_quality_coverage_adapter(
    change_id: str,
    root: Path,
) -> tuple[str, Mapping[str, Any]]:
    """Native adapter: audit only (no synthetic PASS)."""
    report = audit_incremental_quality_coverage(root)
    packet: dict[str, Any] = {**report, "obligation_id": "incremental-quality-coverage", "change_id": change_id}
    status = str(report.get("STATUS", "BLOCKED"))
    if status == "BLOCKED":
        return "EXECUTION_ERROR", {**packet, "code": report.get("REASON", "BLOCKED")}
    if status in ("PASS", "PASS_WITH_GAPS"):
        return "PASS", packet
    return "FAIL", packet
