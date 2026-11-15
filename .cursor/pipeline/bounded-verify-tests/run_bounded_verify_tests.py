# -*- coding: utf-8 -*-
"""Focused test runner for bounded-verify SLICE-001..004 acquisitions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_DIR = Path(__file__).resolve().parent
MODULES = (
    TEST_DIR / "test_plan_freeze.py",
    TEST_DIR / "test_ledger_schedule.py",
    TEST_DIR / "test_executor.py",
    TEST_DIR / "test_result_model.py",
    TEST_DIR / "test_atomic_publication.py",
    TEST_DIR / "test_composition_interaction.py",
    TEST_DIR / "test_split_apply_compose.py",
    TEST_DIR / "test_gate_exit_codes.py",
)


def main() -> int:
    exit_code = 0
    for module in MODULES:
        completed = subprocess.run(
            [sys.executable, str(module)],
            cwd=str(REPO_ROOT),
            check=False,
        )
        if completed.returncode != 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
