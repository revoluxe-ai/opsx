# -*- coding: utf-8 -*-
"""P0-B: S5 receipt validator gate acceptance semantics."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline" / "bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_s5_evidence import (  # noqa: E402
    GateAcceptance,
    validate_evidence_receipt_readonly,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_valid_schema_fail_not_accepted() -> None:
    from bounded_verify_s5_adapters import adapter_implemented

    if not adapter_implemented("pyright-strict-coverage", root=REPO_ROOT):
        return
    with tempfile.TemporaryDirectory() as tmp:
        receipt = Path(tmp) / "r.json"
        _write(
            receipt,
            {
                "evidence_contract": "PYRIGHT_STRICT_COVERAGE_REPORT",
                "STATUS": "FAIL",
                "check_evidence": {"gaps": ["x"]},
            },
        )
        out = validate_evidence_receipt_readonly(
            "pyright-strict-coverage",
            receipt,
            root=REPO_ROOT,
        )
        assert out["schema_valid"] is True
        assert out["gate_acceptance"] == GateAcceptance.REJECTED.value


def test_valid_schema_blocked_not_accepted() -> None:
    from bounded_verify_s5_adapters import adapter_implemented

    if not adapter_implemented("pyright-strict-coverage", root=REPO_ROOT):
        return
    with tempfile.TemporaryDirectory() as tmp:
        receipt = Path(tmp) / "r.json"
        _write(
            receipt,
            {
                "evidence_contract": "PYRIGHT_STRICT_COVERAGE_REPORT",
                "STATUS": "BLOCKED",
            },
        )
        out = validate_evidence_receipt_readonly(
            "pyright-strict-coverage", receipt, root=REPO_ROOT
        )
        assert out["gate_acceptance"] == GateAcceptance.BLOCKED.value


def test_forged_pass_without_check_evidence() -> None:
    from bounded_verify_s5_adapters import adapter_implemented

    if not adapter_implemented("pyright-strict-coverage", root=REPO_ROOT):
        return
    with tempfile.TemporaryDirectory() as tmp:
        receipt = Path(tmp) / "r.json"
        _write(
            receipt,
            {
                "evidence_contract": "PYRIGHT_STRICT_COVERAGE_REPORT",
                "STATUS": "PASS",
            },
        )
        out = validate_evidence_receipt_readonly(
            "pyright-strict-coverage", receipt, root=REPO_ROOT
        )
        assert out["gate_acceptance"] != GateAcceptance.ACCEPTED.value


def test_unsupported_adapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        receipt = Path(tmp) / "r.json"
        _write(
            receipt,
            {
                "evidence_contract": "EXTERNAL_CALL_COVERAGE_REPORT",
                "STATUS": "PASS",
                "check_evidence": {"ok": True},
            },
        )
        out = validate_evidence_receipt_readonly(
            "external-call-coverage", receipt, root=REPO_ROOT
        )
        assert out["gate_acceptance"] == GateAcceptance.UNSUPPORTED.value


def main() -> int:
    test_valid_schema_fail_not_accepted()
    test_valid_schema_blocked_not_accepted()
    test_forged_pass_without_check_evidence()
    test_unsupported_adapter()
    print("s5 evidence validator tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
