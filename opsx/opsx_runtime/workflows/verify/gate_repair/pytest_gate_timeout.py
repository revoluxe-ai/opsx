"""Динамический бюджет subprocess pytest для quality gates и pytest_execute."""

from __future__ import annotations

import json
import re

# Минимальный per-test: pytest-timeout (``--timeout``) и call-phase сторож subprocess.
# Юнит-сьют из тысяч кейсов должен укладываться в секунды; зависший один тест режется здесь.
QUALITY_GATE_PER_TEST_SECONDS = 5.0

# Collect / placeholder, пока точное число тестов неизвестно.
QUALITY_GATE_MIN_TIMEOUT_SECONDS = 30.0

# Для фазы collect, когда точное число тестов ещё неизвестно (только collect-only budget).
QUALITY_GATE_COLLECT_PLACEHOLDER_TEST_COUNT = 20

# Тишина stdout/stderr pytest subprocess без строки хода (§27.0.3 opsx.mdc).
QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS = 300.0

# Запас к stall для крайнего wall-clock backstop subprocess (не 90 с и не N×per_test).
_QUALITY_GATE_SUBPROCESS_SAFETY_TAIL_SECONDS = QUALITY_GATE_PER_TEST_SECONDS * 4.0

_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected", re.IGNORECASE)


def quality_gate_subprocess_safety_seconds() -> float:
    """Крайний потолок subprocess при непрерывном прогрессе (не режет быстрый full suite)."""
    return QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS + _QUALITY_GATE_SUBPROCESS_SAFETY_TAIL_SECONDS


def parse_collected_test_count(collect_log: str) -> int | None:
    """Число собранных тестов из stdout ``pytest --collect-only``."""
    match = _COLLECTED_RE.search(collect_log)
    if match:
        return int(match.group(1))
    if "no tests collected" in collect_log.lower():
        return 0
    return None


def resolve_pytest_gate_timeout_seconds(
    test_count: int,
    *,
    caller_cap_seconds: float | None = None,
) -> float:
    """Бюджет subprocess для collect и редкого suite wall-clock backstop.

    Не использует ``test_count × per_test`` и не обрезает сьют до 90 с: enforcement
    зависания одного кейса — ``pytest-timeout`` и call-phase сторож (``PYTEST_STALL``).
    Тишина без прогресса — ``QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS``.

    ``test_count`` сохранён для совместимости вызовов; на формулу не влияет.

    Caller cap не расширяет бюджет (только ``min``).
    """
    _ = max(0, test_count)
    computed = max(QUALITY_GATE_MIN_TIMEOUT_SECONDS, quality_gate_subprocess_safety_seconds())
    if caller_cap_seconds is not None and caller_cap_seconds > 0:
        return min(computed, float(caller_cap_seconds))
    return computed


def resolve_collect_phase_timeout_seconds(
    *,
    caller_cap_seconds: float | None = None,
) -> float:
    """Таймаут collect-only до того, как известно фактическое число тестов."""
    return resolve_pytest_gate_timeout_seconds(
        QUALITY_GATE_COLLECT_PLACEHOLDER_TEST_COUNT,
        caller_cap_seconds=caller_cap_seconds,
    )


def format_pytest_stall_evidence(
    *,
    scope: str,
    stall_limit_s: float,
    timeout_s: float,
    test_count: int,
    per_test_s: float,
    last_node_id: str,
    stdout_tail: str,
    stderr_tail: str,
    stall_when: str = "unknown",
    hang_product_eligible: bool = False,
    call_limit_s: float | None = None,
    stack_status: str = "unknown",
    stall_function_analysis: dict[str, object] | None = None,
) -> str:
    """Evidence stall-finding: subprocess убит, граф получает ``FAILED`` node id."""
    payload: dict[str, object] = {
        "scope": scope,
        "stall_limit_s": stall_limit_s,
        "timeout_s": timeout_s,
        "test_count": test_count,
        "per_test_s": per_test_s,
        "last_node_id": last_node_id,
        "stall_when": stall_when,
        "hang_product_eligible": hang_product_eligible,
        "stack_status": stack_status,
        "stdout_tail": stdout_tail[-2000:],
        "stderr_tail": stderr_tail[-2000:],
    }
    if call_limit_s is not None and call_limit_s > 0:
        payload["call_limit_s"] = call_limit_s
    if stall_function_analysis is not None:
        payload["stall_function_analysis"] = stall_function_analysis
    return json.dumps(payload, ensure_ascii=False)


def format_pytest_timeout_evidence(
    *,
    scope: str,
    timeout_s: float,
    test_count: int,
    per_test_s: float,
    last_node_id: str,
    stdout_tail: str,
    stderr_tail: str,
) -> str:
    """Evidence suite-timeout finding: subprocess убит, граф получает ``FAILED`` node id."""
    payload = {
        "scope": scope,
        "timeout_s": timeout_s,
        "test_count": test_count,
        "per_test_s": per_test_s,
        "last_node_id": last_node_id,
        "stdout_tail": stdout_tail[-2000:],
        "stderr_tail": stderr_tail[-2000:],
    }
    return json.dumps(payload, ensure_ascii=False)
