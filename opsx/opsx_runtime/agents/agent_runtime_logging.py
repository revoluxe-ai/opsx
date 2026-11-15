"""Централизованное file-logging для OpsX agent runtime hops.

Правило пути log-файла (``agent_log_path_from_state``):

1. Если в GraphState есть непустой ``stream_log`` — файл рядом с NDJSON:
   ``*.live.ndjson`` → ``*.agent_run.log`` (аналогично ``*.conversation.json``).
2. Иначе — ``<repository>/opsx/runs/opsx_agent_runtime.log``; ``change_id``,
   ``thread_id`` и класс агента пишутся в текст сообщения.

``ensure_agent_runtime_logging`` вызывается из ``ABCAgent`` перед каждым hop;
настройка идемпотентна и потокобезопасна (один ``FileHandler`` на logger
``opsx.agent_runtime``, путь обновляется при смене target между hop'ами).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import logging
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "LOG_FORMAT",
    "agent_log_path_for_stream_log",
    "agent_log_path_from_state",
    "ensure_agent_runtime_logging",
    "format_run_context",
]

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
_LOGGER_NAME = "opsx.agent_runtime"

_config_lock = threading.Lock()
_active_path: Path | None = None
_file_handler: logging.FileHandler | None = None


def _repo_root_from_state(state: Any) -> Path:
    if isinstance(state, Mapping):
        for key in ("repository_path", "workspace"):
            raw = state.get(key)
            if raw and str(raw).strip():
                return Path(str(raw)).resolve()
    return Path.cwd().resolve()


def agent_log_path_for_stream_log(stream_log: str | Path) -> Path:
    """Соседний ``*.agent_run.log`` для ``*.live.ndjson`` stream_log."""
    ndjson_path = Path(stream_log)
    target = ndjson_path.with_name(
        ndjson_path.name.replace(".live.ndjson", ".agent_run.log")
    )
    if target.name == ndjson_path.name:
        target = ndjson_path.with_suffix(".agent_run.log")
    return target


def agent_log_path_from_state(state: Mapping[str, Any] | None) -> Path:
    """Вычислить путь agent run log из GraphState / RepairState."""
    if state is not None:
        raw_stream = state.get("stream_log")
        if raw_stream and str(raw_stream).strip():
            return agent_log_path_for_stream_log(str(raw_stream))
    repo = _repo_root_from_state(state)
    runs = resource_path(repo, "opsx", "runs")
    runs.mkdir(parents=True, exist_ok=True)
    return runs / "opsx_agent_runtime.log"


def ensure_agent_runtime_logging(state: Any = None) -> Path:
    """Идемпотентно настроить file logging для agent hops (thread-safe).

    Args:
        state: GraphState или mapping с ``stream_log`` / ``repository_path``.

    Returns:
        Абсолютный путь к log-файлу, в который пишет ``opsx.agent_runtime``.
    """
    mapping = state if isinstance(state, Mapping) else None
    log_path = agent_log_path_from_state(mapping)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    global _active_path, _file_handler
    with _config_lock:
        if _active_path == log_path and _file_handler is not None:
            return log_path

        agent_logger = logging.getLogger(_LOGGER_NAME)
        agent_logger.setLevel(logging.INFO)

        if _file_handler is not None:
            agent_logger.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None

        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.setLevel(logging.INFO)
        agent_logger.addHandler(handler)
        _file_handler = handler
        _active_path = log_path

    return log_path


def format_run_context(state: Any, agent_class: str) -> str:
    """Краткий контекст hop для строк лога (change_id, thread_id, agent)."""
    change_id = ""
    thread_id = ""
    stream_log = ""
    if isinstance(state, Mapping):
        change_id = str(state.get("change_id") or "").strip()
        thread_id = str(state.get("thread_id") or "").strip()
        stream_log = str(state.get("stream_log") or "").strip()
    parts = [f"agent={agent_class}"]
    if change_id:
        parts.append(f"change_id={change_id}")
    if thread_id:
        parts.append(f"thread_id={thread_id}")
    if stream_log:
        parts.append(f"stream_log={stream_log}")
    return " ".join(parts)
