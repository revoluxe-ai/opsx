# -*- coding: utf-8 -*-
"""S5-A fixture evidence semantics (never production S5 PASS)."""

from __future__ import annotations

from typing import Mapping

FIXTURE_EVIDENCE_SCOPE = "S5_FIXTURE_TEST_ONLY"
FIXTURE_STATUS_PASS = "FIXTURE_PASS"
FIXTURE_STATUS_FAIL = "FIXTURE_FAIL"
FIXTURE_STATUS_BLOCKED = "FIXTURE_BLOCKED"

FIXTURE_CONTRACT_SUFFIX = "_FIXTURE_TEST"


def fixture_evidence_contract(base_contract: str) -> str:
    if base_contract.endswith(FIXTURE_CONTRACT_SUFFIX):
        return base_contract
    return f"{base_contract}{FIXTURE_CONTRACT_SUFFIX}"


def fixture_result_status(ok: bool, *, blocked: bool = False) -> str:
    if blocked:
        return FIXTURE_STATUS_BLOCKED
    return FIXTURE_STATUS_PASS if ok else FIXTURE_STATUS_FAIL


def is_production_s5_evidence(doc: Mapping[str, object]) -> bool:
    scope = str(doc.get("evidence_scope", ""))
    status = str(doc.get("STATUS", ""))
    contract = str(doc.get("evidence_contract", ""))
    if scope == FIXTURE_EVIDENCE_SCOPE:
        return False
    if status.startswith("FIXTURE_"):
        return False
    if contract.endswith(FIXTURE_CONTRACT_SUFFIX):
        return False
    if doc.get("production_evidence") is False:
        return False
    return True
