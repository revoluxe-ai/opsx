"""Pytest-плагин: машинные строки прогресса для OPSX gate (setup/call/teardown).

Печатает в stdout префикс ``OPSX_GATE_PROGRESS`` — родительский gate парсит их
независимо от произвольного ``-v`` вывода. На ``pytest_timeout_set_timer`` пишет
фактический ``settings.timeout`` в progress и взводит ``faulthandler`` до убийства
дерева сторожем.
"""

from __future__ import annotations

import faulthandler
import os
import sys
from typing import Any

import pytest

_PREFIX = "OPSX_GATE_PROGRESS"
PROBE_NODE_ENV = "OPSX_PROBE_TIMEOUT_NODE_ID"
PROBE_CEILING_ENV = "OPSX_PROBE_TIMEOUT_CEILING"


def _emit(
    nodeid: str,
    when: str,
    event: str,
    *,
    outcome: str | None = None,
    call_limit_s: float | int | None = None,
) -> None:
    parts = [f"{_PREFIX} node={nodeid} when={when} event={event}"]
    if outcome is not None:
        parts.append(f"outcome={outcome}")
    if call_limit_s is not None:
        parts.append(f"call_limit_s={call_limit_s}")
    line = " ".join(parts)
    print(line, flush=True)


def pytest_configure(config: Any) -> None:
    """Включить faulthandler в gate-subprocess."""
    _ = config
    faulthandler.enable()


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Пробный personal-timeout: потолок на item через официальный marker pytest-timeout."""
    _ = config
    node_target = os.environ.get(PROBE_NODE_ENV, "").strip()
    ceiling_raw = os.environ.get(PROBE_CEILING_ENV, "").strip()
    if not node_target or not ceiling_raw:
        return
    try:
        ceiling = int(float(ceiling_raw))
    except ValueError:
        return
    mark = pytest.mark.timeout(ceiling)
    normalized_target = node_target.replace("\\", "/")
    for item in items:
        if item.nodeid.replace("\\", "/") == normalized_target:
            item.add_marker(mark)


@pytest.hookimpl(trylast=True)
def pytest_timeout_set_timer(item: Any, settings: Any) -> bool | None:
    """Запомнить лимит item и dump traceback на stderr; штатный таймер pytest-timeout не трогаем."""
    timeout = getattr(settings, "timeout", None)
    if timeout is not None and timeout > 0:
        item._opsx_call_limit_s = timeout  # noqa: SLF001
        faulthandler.dump_traceback_later(timeout, repeat=False, file=sys.stderr)
    return None


@pytest.hookimpl(trylast=True)
def pytest_timeout_cancel_timer(item: Any) -> bool | None:
    """Снять отложенный traceback, чтобы он не попал в следующий тест."""
    _ = item
    faulthandler.cancel_dump_traceback_later()
    return None


def pytest_runtest_setup(item: Any) -> None:
    """Старт фазы setup."""
    _emit(item.nodeid, "setup", "start")


def pytest_runtest_call(item: Any) -> None:
    """Старт фазы call (тело теста) с фактическим call_limit из pytest-timeout."""
    limit = getattr(item, "_opsx_call_limit_s", None)
    _emit(item.nodeid, "call", "start", call_limit_s=limit)


def pytest_runtest_teardown(item: Any) -> None:
    """Старт фазы teardown."""
    _emit(item.nodeid, "teardown", "start")


def pytest_runtest_logreport(report: Any) -> None:
    """Итог фазы setup/call/teardown."""
    when = str(getattr(report, "when", "") or "")
    if when not in {"setup", "call", "teardown"}:
        return
    outcome = str(getattr(report, "outcome", "") or "unknown")
    call_limit: float | int | None = None
    if when == "call":
        item = getattr(report, "item", None)
        if item is not None:
            call_limit = getattr(item, "_opsx_call_limit_s", None)
    _emit(report.nodeid, when, "report", outcome=outcome, call_limit_s=call_limit)
