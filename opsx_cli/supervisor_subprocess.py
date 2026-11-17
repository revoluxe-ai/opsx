"""Kill process tree и запуск worker с watchdog по stdout."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if os.name == "nt":
    _TASKKILL = ["taskkill", "/PID", "{pid}", "/T", "/F"]
else:
    _TASKKILL = None


async def kill_process_tree(pid: int) -> None:
    """Завершить процесс и дочерние (Windows taskkill /T, POSIX process group)."""
    if pid <= 0:
        return

    def _kill() -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass

    await asyncio.to_thread(_kill)


async def run_worker_with_watchdog(
    *,
    interpreter: str,
    request_path: Path,
    log_path: Path,
    cwd: Path,
    env: dict[str, str],
    wall_clock_limit: float,
    silence_limit: float,
    echo: bool = False,
) -> tuple[int, str | None]:
    """Запустить worker, писать stdout в log; вернуть (exit_code, hang_reason|None)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # truncate/append handled by supervisor parent on main log; here append
    process = await asyncio.create_subprocess_exec(
        interpreter,
        "-m",
        "opsx_cli.worker",
        str(request_path),
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    started = time.monotonic()
    last_line_at = started
    hang_reason: str | None = None

    async def reader() -> None:
        nonlocal last_line_at
        while True:
            chunk = await process.stdout.read(65536)
            if not chunk:
                break
            last_line_at = time.monotonic()

            def append() -> None:
                with log_path.open("ab") as stream:
                    stream.write(chunk)

            await asyncio.to_thread(append)
            if echo:
                await asyncio.to_thread(sys.stdout.write, chunk.decode("utf-8", "replace"))
                await asyncio.to_thread(sys.stdout.flush)

    read_task = asyncio.create_task(reader())
    try:
        while process.returncode is None:
            await asyncio.sleep(0.25)
            now = time.monotonic()
            if now - started > wall_clock_limit:
                hang_reason = "WORKFLOW_WALL_CLOCK_TIMEOUT"
                await kill_process_tree(process.pid or -1)
                break
            if now - last_line_at > silence_limit:
                hang_reason = "SILENCE_TIMEOUT"
                await kill_process_tree(process.pid or -1)
                break
            try:
                await asyncio.wait_for(process.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            break
    finally:
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                await kill_process_tree(process.pid or -1)
                await process.wait()
        read_task.cancel()
        try:
            await read_task
        except asyncio.CancelledError:
            pass

    code = process.returncode if process.returncode is not None else 1
    return int(code), hang_reason


def read_log_tail(log_path: Path, *, max_bytes: int = 64_000) -> str:
    if not log_path.is_file():
        return ""
    size = log_path.stat().st_size
    with log_path.open("rb") as stream:
        if size > max_bytes:
            stream.seek(size - max_bytes)
        data = stream.read()
    return data.decode("utf-8", "replace")