"""DEBUG workflow-логи на stderr до первого agent stream (без ``basicConfig``).

``ensure_workflow_stderr_logging`` идемпотентно вешает ``StreamHandler`` на
родительские логгеры ``workflows`` и ``opsx.verify``, плюс ``langgraph`` на
WARNING — тот же OPSX-формат relpath, что у cursor stream.
"""

from __future__ import annotations

import logging
import threading

from agents.log_setup import ensure_langgraph_stderr_logging, ensure_opsx_stderr_logger

__all__ = ["ensure_workflow_stderr_logging"]

_CONFIGURED = False
_LOCK = threading.Lock()
_WORKFLOW_LOGGER_NAMES = ("workflows", "opsx.verify")


def ensure_workflow_stderr_logging() -> None:
    """Подключить DEBUG workflow-логи и WARNING langgraph к stderr (один раз на процесс)."""
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return
        for logger_name in _WORKFLOW_LOGGER_NAMES:
            ensure_opsx_stderr_logger(logging.getLogger(logger_name), level=logging.DEBUG)
        ensure_langgraph_stderr_logging()
        _CONFIGURED = True
