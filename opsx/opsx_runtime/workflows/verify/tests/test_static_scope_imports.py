"""Import-order regression: quality gates must not import a half-built GroupError."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "code",
    [
        (
            "from workflows.verify.gate_repair import runner\n"
            "from agents.verify.group_error.agent import GroupErrorAgent\n"
            "from workflows.verify.gate_repair.static_scope import "
            "gate_issue_in_product_static_scope\n"
            "assert gate_issue_in_product_static_scope('src/stroy/app.py:10: error')\n"
            "assert not gate_issue_in_product_static_scope('tests/test_app.py:10: error')\n"
        ),
        (
            "from agents.verify.group_error.agent import GroupErrorAgent\n"
            "from workflows.verify.gate_repair import runner\n"
            "from workflows.verify.gate_repair.static_scope import "
            "gate_issue_in_product_static_scope\n"
            "assert gate_issue_in_product_static_scope('src/stroy/app.py:10: error')\n"
        ),
    ],
)
def test_quality_gate_and_group_error_import_without_cycle(code: str) -> None:
    """Isolate import order in fresh Python processes (no pytest module cache)."""
    runtime_root = Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(runtime_root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Import order failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
