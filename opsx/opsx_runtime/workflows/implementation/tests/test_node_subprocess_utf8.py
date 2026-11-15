"""UTF-8 decode дочернего stdout: не cp1251 UnicodeDecodeError."""

from __future__ import annotations

import sys

import pytest

from workflows.implementation.node_subprocess import run_subprocess_utf8, subprocess_utf8_env


@pytest.mark.unit
def test_child_non_cp1251_stdout_does_not_raise() -> None:
    """Байт 0x98 (невалиден в cp1251) не роняет reader при UTF-8 decode."""
    cmd = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'line\\x98\\xff\\n'); sys.stderr.buffer.write(b'err\\x98\\n')",
    ]
    proc = run_subprocess_utf8(cmd, timeout=30)
    assert proc.returncode == 0
    assert "line" in proc.stdout
    assert "\ufffd" in proc.stdout or "\x98" in proc.stdout
    assert "err" in proc.stderr


@pytest.mark.unit
def test_subprocess_utf8_env_for_child_stdio() -> None:
    """Дочерний Python получает UTF-8 stdio, не code page Windows."""
    env = subprocess_utf8_env()
    assert env.get("PYTHONUTF8") == "1"
    assert "utf-8" in (env.get("PYTHONIOENCODING") or "")
