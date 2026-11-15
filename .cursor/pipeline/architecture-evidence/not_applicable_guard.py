#!/usr/bin/env python3
"""Independent NOT_APPLICABLE checks (S4A)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from discovery import discover_for_change

_TA_REQUIRED = re.compile(r"(?m)^TECHNOLOGY_ASSESSMENT\s*\nREQUIRED:\s*yes\b", re.I)


@dataclass
class NaFinding:
    code: str
    detail: str


def validate_not_applicable_independent(
    receipt: dict[str, Any],
    *,
    change_root: Path,
    repo_root: Path,
    design_text: str,
    design_obligations: list[dict[str, str]],
) -> list[NaFinding]:
    """Reject false NOT_APPLICABLE when material coverage signals exist."""

    if receipt.get("applicability") != "NOT_APPLICABLE":
        return []
    findings: list[NaFinding] = []
    if not receipt.get("scope_rationale"):
        findings.append(NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "missing scope_rationale"))
    if receipt.get("claims"):
        findings.append(NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "NOT_APPLICABLE must not carry claims"))
    if design_obligations:
        findings.append(
            NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "design declares coverage obligations")
        )
    if _TA_REQUIRED.search(design_text):
        findings.append(NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "TECHNOLOGY_ASSESSMENT REQUIRED:yes"))
    discovery = discover_for_change(change_root, repo_root)
    if discovery.operations:
        findings.append(
            NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "discovery found material operations")
        )
    if discovery.blockers:
        findings.append(
            NaFinding("FALSE_NOT_APPLICABLE_REJECTED", "discovery blockers present")
        )
    return findings
