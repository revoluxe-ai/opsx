"""Seed ARCH-EVIDENCE.json из design obligations (без auto-PASS)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_MODULE_PATH = Path(__file__).resolve().parent / "architecture_evidence.py"


def _oracle() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architecture_evidence", _MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def seed_receipt_from_design(
    *,
    change_id: str,
    change_root: Path,
    design_path: Path,
    design_writer_id: str,
    applicability: str = "REQUIRED",
) -> dict[str, Any]:
    """Собрать черновик receipt с UNKNOWN claims и пустыми sources."""

    oracle = _oracle()
    design_text = design_path.read_text(encoding="utf-8")
    obligations = oracle.parse_design_coverage_obligations(design_text)
    claims: list[dict[str, Any]] = []
    for item in obligations:
        claims.append(
            {
                "claim_id": f"seed-{item['obligation_id']}",
                "category": item["category"],
                "design_anchor": item.get("design_anchor", ""),
                "obligation_id": item["obligation_id"],
                "result": "UNKNOWN",
                "failure_code": None,
                "sources": [],
            }
        )
    coverage = [
        {
            "obligation_id": item["obligation_id"],
            "category": item["category"],
            "design_anchor": item.get("design_anchor", ""),
        }
        for item in obligations
    ]
    return {
        "schema_version": oracle.SCHEMA_VERSION,
        "contract_id": oracle.CONTRACT_ID,
        "change_id": change_id,
        "applicability": applicability,
        "design_writer_id": design_writer_id,
        "design_digest": oracle.sha256_file(design_path),
        "inputs_digest": oracle.compute_inputs_digest(change_root),
        "execution_identity": {
            "validator_implementation_id": oracle.VALIDATOR_IMPLEMENTATION_ID,
            "schema_version": oracle.SCHEMA_VERSION,
        },
        "verifier": {
            "kind": "INDEPENDENT_TOOL",
            "id": oracle.VALIDATOR_IMPLEMENTATION_ID,
        },
        "coverage_obligations": coverage,
        "claims": claims,
        "summary": {
            "ready_for_design_freeze": False,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    }
