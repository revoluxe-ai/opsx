# -*- coding: utf-8 -*-
"""S6 entrypoint: delegate to real Temporal/bounded-verify state machine tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_MACHINE = REPO_ROOT / "tools/temporal/tests/test_bounded_verify_state_machine.py"


def main() -> int:
    if not STATE_MACHINE.is_file():
        print("PARTIAL: state machine tests missing")
        return 1
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(STATE_MACHINE), "-q"],
        cwd=REPO_ROOT,
        check=False,
    )
    if proc.returncode != 0:
        print("PARTIAL: some S6 temporal/state-machine tests failed")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
