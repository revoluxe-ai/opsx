"""Единый stderr-формат OPSX для module-логгеров (relpath:lineno, без root propagate)."""

from __future__ import annotations

from opsx.project_context import project_root

import logging
import sys
from pathlib import Path
from typing import ClassVar

OPSX_STDERR_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(relpath)s:%(lineno)s %(name)s %(message)s"
)

__all__ = [
    "OPSX_STDERR_LOG_FORMAT",
    "OpsxStderrFormatter",
    "StaleVerifyCycleGateLogFilter",
    "create_opsx_stderr_formatter",
    "ensure_langgraph_stderr_logging",
    "ensure_opsx_stderr_logger",
]


def _default_repo_root() -> Path:
    """Корень репозитория OPSX (родитель каталога ``opsx``)."""
    return project_root()


def create_opsx_stderr_formatter(
    repo_root: Path | None = None,
) -> OpsxStderrFormatter:
    """Собрать форматтер stderr с путём файла относительно корня репозитория."""
    return OpsxStderrFormatter(repo_root=repo_root)


class OpsxStderrFormatter(logging.Formatter):
    """Форматтер OPSX: ``record.relpath`` — путь от корня repo, иначе ``record.pathname``."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        fmt: str = OPSX_STDERR_LOG_FORMAT,
    ) -> None:
        super().__init__(fmt=fmt)
        self._repo_root = (repo_root or _default_repo_root()).resolve()

    def format(self, record: logging.LogRecord) -> str:
        pathname = Path(record.pathname)
        try:
            relpath = pathname.resolve().relative_to(self._repo_root).as_posix()
        except ValueError:
            relpath = pathname.as_posix()
        record.relpath = relpath
        return super().format(record)


class StaleVerifyCycleGateLogFilter(logging.Filter):
    """Один раз дополняет LangGraph про ``branch:to:verify_cycle_gate`` OPSX-контекстом (DEBUG)."""

    _logged: ClassVar[bool] = False

    def filter(self, record: logging.LogRecord) -> bool:
        if StaleVerifyCycleGateLogFilter._logged:
            return True
        if "unknown channel branch:to:verify_cycle_gate" not in record.getMessage():
            return True
        StaleVerifyCycleGateLogFilter._logged = True
        interpret = logging.getLogger("workflows.checkpoint_resume")
        interpret.debug(
            "checkpoint interpret: LangGraph ignored branch:to:verify_cycle_gate "
            "(устаревший channel в durable checkpoint при чтении state/history; "
            "это не crash runtime, resume может продолжиться или завершиться terminal exit)",
        )
        return True


def ensure_opsx_stderr_logger(
    logger: logging.Logger,
    *,
    level: int = logging.DEBUG,
) -> logging.Logger:
    """Идемпотентно повесить StreamHandler(stderr) с OPSX-форматом на ``logger``."""
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(create_opsx_stderr_formatter())
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(level)
    return logger


def ensure_langgraph_stderr_logging() -> None:
    """Подключить ``langgraph`` WARNING на stderr (pregel unknown channel и т.п.)."""
    target = logging.getLogger("langgraph")
    ensure_opsx_stderr_logger(target, level=logging.WARNING)
    stale_filter = StaleVerifyCycleGateLogFilter()
    for handler in target.handlers:
        handler.addFilter(stale_filter)
