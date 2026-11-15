# -*- coding: utf-8 -*-
"""P0-D: production workspace must reject synthetic IMPL even with token+env."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_impl_completion import (  # noqa: E402
    fetch_impl_completion_report,
    hermetic_test_authority_granted,
)


def test_production_root_rejects_hermetic_authority() -> None:
    os.environ["BOUNDED_VERIFY_HERMETIC_IMPL"] = "1"
    token = REPO_ROOT / ".cursor/pipeline/bounded-verify-tests/fixtures/test-authority.token"
    assert token.is_file()
    assert hermetic_test_authority_granted(root=REPO_ROOT) is False


def main() -> int:
    test_production_root_rejects_hermetic_authority()
    print("hermetic isolation negative tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
