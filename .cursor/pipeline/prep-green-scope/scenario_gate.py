"""Scenario gate — re-exports authority from pipeline contract module."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CONTRACTS = _REPO / ".cursor" / "pipeline" / "contracts"
if str(_CONTRACTS) not in sys.path:
    sys.path.insert(0, str(_CONTRACTS))

from required_test_ids_gate import (  # noqa: E402
    RequiredTestSkipVerdict,
    audit_required_test_ids,
    audit_required_test_ids_row,
    test_id_is_skipped,
)
from required_static_check_gate import (  # noqa: E402
    RequiredStaticCheckVerdict,
    audit_required_static_checks,
    mypy_module_is_ignore_errors,
    pyright_file_is_ignored,
)

__all__ = [
    "RequiredTestSkipVerdict",
    "RequiredStaticCheckVerdict",
    "audit_required_test_ids",
    "audit_required_test_ids_row",
    "audit_required_static_checks",
    "test_id_is_skipped",
    "pyright_file_is_ignored",
    "mypy_module_is_ignore_errors",
]
