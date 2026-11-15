"""Authority for /opsx-spec-scenario-coverage mode=audit GATE_INTEGRITY (inspect-only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from change_implementation_paths import derive_change_required_production_paths
from required_static_check_gate import (
    RequiredStaticCheckVerdict,
    audit_required_static_checks,
)


@dataclass
class ScenarioAuditGateIntegrity:
    """Whole-change GATE_INTEGRITY slice for static prep-green suppressions."""

    change_id: str
    change_required_paths: list[str]
    gate_integrity: str
    required_static_check_rows: list[RequiredStaticCheckVerdict]

    @property
    def gap_class(self) -> str | None:
        if self.required_static_check_rows:
            return "REQUIRED_STATIC_CHECK_DISABLED"
        return None


def audit_gate_integrity(
    repo_root: Path,
    change_id: str,
) -> ScenarioAuditGateIntegrity:
    """GATE_INTEGRITY static slice: prep-green suppressions on change-required paths."""

    paths = derive_change_required_production_paths(repo_root, change_id)
    rows = audit_required_static_checks(repo_root, paths)
    gate = "FAIL" if rows else "PASS"
    return ScenarioAuditGateIntegrity(
        change_id=change_id,
        change_required_paths=paths,
        gate_integrity=gate,
        required_static_check_rows=rows,
    )
