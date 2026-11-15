"""Терминальный PAUSE implementation hop при неизвестном исходе adapter call."""

from __future__ import annotations

import asyncio
from typing import Any

from agents.adapters.abc_adapter import ActiveCursorAgentRunError
from workflows.implementation.routing_contract import RUN_STATUS_PAUSED

SDK_BRIDGE_TIMEOUT = "SDK_BRIDGE_TIMEOUT"
SDK_ACTIVE_RUN_CONFLICT = "SDK_ACTIVE_RUN_CONFLICT"

# Typed PAUSE до вызова mutating adapter agent — не считается завершённым hop.
PRE_AGENT_EXECUTE_PAUSE_CODES = frozenset(
    {
        "STAGE_NOT_WIRED",
        "DISPATCH_LEDGER_ACTIVE_HOP",
    }
)


def _is_bridge_timeout_exception(exc: BaseException | None) -> bool:
    """True, если исключение — timeout bridge (прямо или через ``__cause__`` / wrap)."""
    if exc is None:
        return False
    name = type(exc).__name__
    if name in ("APITimeoutError", "TimeoutError") or isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, ActiveCursorAgentRunError):
        if exc.code == SDK_BRIDGE_TIMEOUT or exc.reason == SDK_BRIDGE_TIMEOUT:
            return True
        if exc.__cause__ is not None:
            return _is_bridge_timeout_exception(exc.__cause__)
    return False


def resolve_adapter_outcome_unknown_code(exc: BaseException | None) -> str:
    """Классифицировать PAUSE: bridge timeout vs конфликт активного run."""
    if _is_bridge_timeout_exception(exc):
        return SDK_BRIDGE_TIMEOUT
    return SDK_ACTIVE_RUN_CONFLICT


def patch_implementation_execute_terminal_pause(
    *,
    stage: str,
    slice_id: str | None,
    recovery_code: str,
    message: str,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Терминальный PAUSE execute-hop с явным ``recovery_code``.

    ``implementation_last_hop`` выставляется только если hop дошёл до адаптера
    (есть ``run_id``). Pre-agent gate (``STAGE_NOT_WIRED``, dispatch ledger)
    не должен помечать stage как уже выполненный — иначе stale resume уходит
    снова в ``implementation_route`` / plan.
    """
    stored_run_id = str(run_id).strip() if run_id else ""
    stored_agent_id = str(agent_id).strip() if agent_id else ""
    patch: dict[str, Any] = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": recovery_code,
        "implementation_stage": stage,
        "implementation_slice_id": slice_id,
        "implementation_plan_reject_code": recovery_code,
        "error": message,
        "run_id": stored_run_id,
        "agent_id": stored_agent_id,
        "implementation_machine_gate_errors": [
            {
                "code": recovery_code,
                "message": message,
                "agent_id": stored_agent_id or None,
                "run_id": stored_run_id or None,
            },
        ],
    }
    if stored_run_id and recovery_code not in PRE_AGENT_EXECUTE_PAUSE_CODES:
        patch["implementation_last_hop"] = stage
    return patch


def patch_implementation_adapter_outcome_unknown(
    *,
    stage: str,
    slice_id: str | None,
    agent_id: str | None,
    run_id: str | None = None,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Не бросать из graph: PAUSE с run/agent с исключения, не из state предыдущего hop.

    Каналы ``run_id`` и ``agent_id`` перезаписываются всегда. Пустая строка —
    честный unknown: отсутствие ключа оставило бы id предыдущего hop в checkpoint.
    """
    detail = f"{type(exc).__name__}: {exc}" if exc is not None else "unknown"
    code = resolve_adapter_outcome_unknown_code(exc)
    if isinstance(exc, ActiveCursorAgentRunError):
        agent_id = agent_id or exc.agent_id
        if run_id is None:
            run_id = exc.run_id
    stored_run_id = str(run_id).strip() if run_id else ""
    stored_agent_id = str(agent_id).strip() if agent_id else ""
    report = (
        f"{code}: stage={stage} slice={slice_id or '-'} "
        f"agent_id={stored_agent_id or 'unknown'} run_id={stored_run_id or 'unknown'}; "
        f"Adapter hop outcome UNKNOWN ({detail}). "
        "Do not start a second mutating hop until the prior run is reconciled."
    )
    patch: dict[str, Any] = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": code,
        "implementation_stage": stage,
        "implementation_slice_id": slice_id,
        "implementation_plan_reject_code": code,
        "error": report,
        "run_id": stored_run_id,
        "agent_id": stored_agent_id,
        "implementation_machine_gate_errors": [
            {
                "code": code,
                "message": report,
                "agent_id": stored_agent_id or None,
                "run_id": stored_run_id or None,
            },
        ],
    }
    if stored_run_id:
        patch["implementation_last_hop"] = stage
    return patch
