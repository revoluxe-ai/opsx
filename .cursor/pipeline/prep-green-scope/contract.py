"""Версии и идентичность контракта /opsx-prep-green-scope (process-only)."""

from __future__ import annotations

COMMAND_CONTRACT_VERSION = "0.9.3"
COMMAND_CONTRACT_ID = "OPSX-PREP-GREEN-SCOPE-V1"
IMPLEMENTATION_ID = "stroy-prep-green-scope-v0.9.3-agent"

PROBE_SCHEMA = "opsx.prep_green_scope.probe.v2"
PLAN_SCHEMA = "opsx.prep_green_scope.plan.v2"
APPLY_SCHEMA = "opsx.prep_green_scope.apply.v2"

STALE_PLAN_FIELDS = frozenset(
    {
        "pytest_deselect_count",
        "mypy_ignore_errors_global",
    }
)

STALE_PROBE_GATE_NOTES = (
    "addopts --deselect",
    "integration/destructive: SKIPPED after unit FAIL",
)
