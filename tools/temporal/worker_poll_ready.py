"""Маркер готовности Temporal worker к poll task queue (не только наличие PID).

`ensure_worker` раньше считал worker «listening», как только в `ps` появлялся
`worker.py`, хотя `connect_temporal_client()` и регистрация poller ещё не
завершены. Файл `.tmp/temporal/worker-poll-ready.json` пишется из `worker.py`
непосредственно перед `worker.run()`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from run_store import scratch_temporal_dir


def poll_ready_path(repo_root: str | Path) -> Path:
    """Путь к JSON-маркеру poll-ready для репозитория."""

    return scratch_temporal_dir(repo_root) / "worker-poll-ready.json"


def write_poll_ready(
    repo_root: str | Path,
    *,
    pid: int,
    task_queue: str,
    control_plane_code_identity: str,
) -> None:
    """Записать маркер: процесс подключён к Temporal и вот-вот начнёт poll."""

    path = poll_ready_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": int(pid),
        "task_queue": task_queue,
        "control_plane_code_identity": control_plane_code_identity,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clear_poll_ready(repo_root: str | Path) -> None:
    """Удалить маркер при завершении worker (best-effort)."""

    path = poll_ready_path(repo_root)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def read_poll_ready(repo_root: str | Path) -> dict[str, Any] | None:
    """Прочитать маркер poll-ready; `None` если файла нет или JSON битый."""

    path = poll_ready_path(repo_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def process_is_alive(pid: int) -> bool:
    """Best-effort check that ``pid`` still refers to a running process."""

    if int(pid) <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def repo_poll_ready_live_pid(repo_root: str | Path) -> int | None:
    """PID из poll-ready маркера, если процесс ещё жив (Windows-safe)."""

    data = read_poll_ready(repo_root)
    if not data:
        return None
    try:
        ready_pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return None
    if not process_is_alive(ready_pid):
        return None
    return ready_pid


def worker_is_polling(pid: int, repo_root: str | Path) -> bool:
    """True, если маркер существует и PID совпадает с живым worker."""

    data = read_poll_ready(repo_root)
    if not data:
        return False
    try:
        ready_pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return False
    if ready_pid != int(pid):
        return False
    return process_is_alive(ready_pid)
