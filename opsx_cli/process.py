"""Bounded subprocess execution without shell interpolation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .context import OpsxError


@dataclass(frozen=True)
class ProcessResult:
    code: int
    stdout: str
    stderr: str


async def run(
    argv: Sequence[str | Path], *, cwd: Path, timeout: float = 120,
    env: Mapping[str, str] | None = None, check: bool = True,
) -> ProcessResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *(str(v) for v in argv), cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise OpsxError("EXECUTABLE_MISSING", f"Executable not found: {argv[0]}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    result = ProcessResult(proc.returncode, out.decode("utf-8", "replace").strip(),
                           err.decode("utf-8", "replace").strip())
    if check and result.code:
        raise OpsxError("COMMAND_FAILED", f"{argv[0]} exited {result.code}: {result.stderr or result.stdout}")
    return result


async def git(root: Path, *argv: str, check: bool = True) -> ProcessResult:
    return await run(["git", *argv], cwd=root, check=check)
