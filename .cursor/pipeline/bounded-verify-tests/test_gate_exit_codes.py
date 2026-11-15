# -*- coding: utf-8 -*-
"""Gate exit codes: PG provider BLOCKED without receipt."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / ".cursor/pipeline/bounded-verify/gates/run_gate.py"


def main() -> int:
    env = {**os.environ, "BOUNDED_VERIFY_CHANGE_ID": "invest-yalta-max-funnel"}
    completed = subprocess.run(
        [sys.executable, str(GATE), "opsx-pg-provider-receipt"],
        cwd=str(REPO_ROOT),
        env=env,
        check=False,
    )
    if completed.returncode != 3:
        print(f"expected exit 3 BLOCKED, got {completed.returncode}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
