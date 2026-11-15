"""Subprocess Node CLI: stdout/stderr всегда UTF-8, не code page консоли Windows."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path


def decode_subprocess_output(raw: bytes | None) -> str:
    """Декодировать байты stdout/stderr дочернего процесса (без cp1251)."""
    return (raw or b"").decode("utf-8", errors="replace")


def subprocess_utf8_env(
    extra: Mapping[str, str] | None = None,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Окружение дочернего процесса: Python stdio в UTF-8 (не code page консоли)."""
    env = dict(base if base is not None else os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8:replace")
    if extra is not None:
        env.update(extra)
    return env


def run_subprocess_utf8(
    cmd: list[str],
    *,
    input_text: str | None = None,
    cwd: str | Path | None = None,
    timeout: int | float | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` с ``text=False`` и UTF-8 decode на выходе.

    Args:
        cmd: argv дочернего процесса.
        input_text: stdin как UTF-8 bytes (если задан).
        cwd: рабочая директория.
        timeout: секунды.
        env: дополнительные переменные поверх UTF-8 defaults.

    Returns:
        CompletedProcess с str stdout/stderr (replacement на невалидных UTF-8).
    """
    kwargs: dict[str, object] = {
        "args": cmd,
        "capture_output": True,
        "text": False,
        "check": False,
        "env": subprocess_utf8_env(env),
    }
    if cwd is not None:
        kwargs["cwd"] = str(cwd)
    if timeout is not None:
        kwargs["timeout"] = timeout
    if input_text is not None:
        kwargs["input"] = input_text.encode("utf-8")
    raw = subprocess.run(**kwargs)
    return subprocess.CompletedProcess(
        args=raw.args,
        returncode=raw.returncode or 0,
        stdout=decode_subprocess_output(raw.stdout),
        stderr=decode_subprocess_output(raw.stderr),
    )
