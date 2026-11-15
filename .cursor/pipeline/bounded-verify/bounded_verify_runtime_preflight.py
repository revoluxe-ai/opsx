# -*- coding: utf-8 -*-
"""Execution preflight: tool availability vs product assertion failures."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class PreflightOutcome(str, Enum):
    READY = "READY"
    TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"
    TEMPORAL_UNAVAILABLE = "TEMPORAL_UNAVAILABLE"


@dataclass(frozen=True)
class PreflightReport:
    outcome: PreflightOutcome
    message: str
    details: tuple[str, ...] = ()


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def preflight_static_tools(*, require_temporal: bool = False) -> PreflightReport:
    """Check pytest/pyright/mypy availability; missing tools are not product FAIL."""
    missing: list[str] = []
    for tool in ("python3", "pytest"):
        if not _which(tool):
            missing.append(tool)
    if missing:
        return PreflightReport(
            outcome=PreflightOutcome.TOOL_UNAVAILABLE,
            message="required harness tools missing",
            details=tuple(missing),
        )
    if require_temporal:
        proc = subprocess.run(
            ["python3", "-c", "import temporalio"],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return PreflightReport(
                outcome=PreflightOutcome.TEMPORAL_UNAVAILABLE,
                message="temporalio not importable",
            )
    return PreflightReport(outcome=PreflightOutcome.READY, message="ok")
