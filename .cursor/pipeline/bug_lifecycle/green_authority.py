"""Authoritative GREEN evidence validation (reject synthetic / hermetic in production)."""

from __future__ import annotations

import re
from pathlib import Path

from .bug_team_evidence import (
    AUTHORIZED_GREEN_PROVENANCE,
    HERMETIC_PROVENANCE,
    validate_green_binding,
)

GREEN_CONFIRMED = re.compile(r"^STATUS:\s*GREEN_CONFIRMED\s*$", re.I | re.M)
FORBIDDEN_PROVENANCE = re.compile(rf"^PROVENANCE:\s*{HERMETIC_PROVENANCE}\s*$", re.I | re.M)
REQUIRED_FIELDS = (
    "TEST_ID:",
    "BUG_CASE_ID:",
    "FINDING_ID:",
    "TEST_CONTENT_SHA256:",
)


def validate_green_file(path: Path, *, production: bool) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing_green_file"
    text = path.read_text(encoding="utf-8")
    if not GREEN_CONFIRMED.search(text):
        return False, "green_not_confirmed"
    for field in REQUIRED_FIELDS:
        if field not in text:
            return False, f"missing_{field.rstrip(':').lower()}"
    if production and FORBIDDEN_PROVENANCE.search(text):
        return False, "synthetic_green_provenance"
    if production:
        prov = re.search(r"^PROVENANCE:\s*(\S+)\s*$", text, re.I | re.M)
        provenance = prov.group(1) if prov else ""
        if provenance not in AUTHORIZED_GREEN_PROVENANCE:
            return False, "green_provenance_unauthorized"
        if not re.search(r"^WORKER_EXECUTION_ID:\s*\S+\s*$", text, re.I | re.M):
            return False, "green_execution_binding_missing"
    return True, "ok"


def validate_green_file_bound(
    path: Path,
    *,
    case_id: str,
    finding_id: str,
    execution_id: str,
    production: bool,
) -> tuple[bool, str]:
    return validate_green_binding(
        path,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        production=production,
    )
