"""Provider-independent transport contract; ABCAgent owns validation/retry."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agents.config import ReadScope, ReadScopeViolation, resolve_effective_read_scope
from agents.response_schema import response_schema_section

_LOGGER = logging.getLogger(__name__)


class AgentRunContractError(RuntimeError):
    """Machine-readable terminal error of one SDK run or response contract.

    The exception is a fail-closed recovery boundary. A caller must not infer
    a successful verify verdict or silently re-dispatch a model from it.
    """

    def __init__(
        self,
        code: str,
        *,
        status: str,
        run_id: str,
        agent_id: str,
        attempt: int,
        message: str,
    ) -> None:
        self.code = code
        self.status = status
        self.run_id = run_id
        self.agent_id = agent_id
        self.attempt = attempt
        self.message = message
        super().__init__(
            f"{code}: {message}; run_id={run_id or '-'} "
            f"agent_id={agent_id or '-'} status={status or '-'} attempt={attempt}"
        )


TERMINAL_SDK_STATUSES = frozenset(
    {"COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DONE"},
)


class ActiveCursorAgentRunError(RuntimeError):
    """Неизвестный исход adapter hop: активный run или обрыв bridge.

    Типичные случаи:
    - send отклонён, т.к. у resumed session уже есть активный run;
    - timeout/обрыв stream после принятого send — нужны ``run_id`` и ``code``.

    Старые вызовы ``ActiveCursorAgentRunError(agent_id)`` остаются валидными.
    """

    def __init__(
        self,
        agent_id: str,
        run_id: str | None = None,
        *,
        code: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Сохранить agent/run для reconcile; ``code`` — machine recovery (например SDK_BRIDGE_TIMEOUT)."""
        self.agent_id = agent_id
        self.run_id = run_id
        self.code = code or reason
        self.reason = reason or code
        if code == "SDK_BRIDGE_TIMEOUT" or reason == "SDK_BRIDGE_TIMEOUT":
            msg = (
                f"Adapter bridge timeout for agent {agent_id}; "
                f"run_id={run_id or 'unknown'}; outcome UNKNOWN"
            )
        elif run_id:
            msg = (
                f"Adapter agent {agent_id} hop outcome UNKNOWN "
                f"(run_id={run_id}); do not start a second mutating send"
            )
        else:
            msg = (
                f"Adapter agent {agent_id} already has an active run; "
                "outcome and workspace writes are UNKNOWN"
            )
        super().__init__(msg)


@dataclass(frozen=True)
class AgentResult:
    """Итог одного adapter hop; ``agent_id`` — server id адаптера для resume."""

    text: str
    status: str
    agent_id: str
    run_id: str
    usage: dict[str, int | None] | None = None
    cost: dict[str, float] | None = None
    usage_source: str = "unavailable"
    cost_status: str = "pending"
    billing_error: str | None = None
    parsed: BaseModel | None = None
    validation_attempts: int = 1
    # Первое ``outside_restrict_allow`` на hop (до voluntary BLOCKED модели).
    recorded_outside_restrict_allow: ReadScopeViolation | None = None


def normalize_resume_agent_id(value: Any) -> str | None:
    """``None`` → новый SDK agent; непустая строка → ``AsyncAgent.resume``."""
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def workflow_execution_id(state: Mapping[str, Any]) -> str:
    """Идентификатор прогона workflow (``run_uuid`` и алиасы)."""
    for key in ("run_uuid", "workflow_execution_id", "session_id"):
        raw = state.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def session_run_uuid_key(agent_id_key: str) -> str:
    """GraphState-ключ привязки SDK session к ``workflow_execution_id``."""
    if agent_id_key == "agent_id":
        return "session_run_uuid"
    if agent_id_key.endswith("_agent_id"):
        return agent_id_key.replace("_agent_id", "_session_run_uuid")
    return f"{agent_id_key}_session_run_uuid"


def resolve_bound_resume_agent_id(
    state: Mapping[str, Any],
    *,
    stored_agent_id: Any,
    bound_run_uuid: Any,
) -> str | None:
    """Resume только если ``bound_run_uuid`` совпадает с текущим execution."""
    resume = normalize_resume_agent_id(stored_agent_id)
    if resume is None:
        return None
    exec_id = workflow_execution_id(state)
    bound = str(bound_run_uuid or "").strip()
    if not exec_id or not bound or exec_id != bound:
        return None
    return resume


def agent_session_patch(
    state: Mapping[str, Any],
    *,
    agent_id: str,
    agent_id_key: str,
) -> dict[str, Any]:
    """Сохранить server ``agent_id`` и execution, в котором он создан."""
    return {
        agent_id_key: str(agent_id).strip(),
        session_run_uuid_key(agent_id_key): workflow_execution_id(state),
    }


def resume_for_role(
    state: Mapping[str, Any],
    agent_id_key: str,
) -> str | None:
    """Resume SDK session роли в рамках текущего ``run_uuid``."""
    bound_key = session_run_uuid_key(agent_id_key)
    return resolve_bound_resume_agent_id(
        state,
        stored_agent_id=state.get(agent_id_key),
        bound_run_uuid=state.get(bound_key),
    )


def resume_when_same_finding(
    state: Mapping[str, Any],
    *,
    stored_agent_id: Any,
    bound_run_uuid: Any,
    prior_result: Any,
    finding_id: str,
    unit_id: str | None = None,
) -> str | None:
    """Resume для dispatcher item: execution + finding (+ optional unit)."""
    fid = str(finding_id or "").strip()
    if not fid:
        return None
    if not isinstance(prior_result, Mapping):
        return None
    if str(prior_result.get("finding_id") or "").strip() != fid:
        return None
    if unit_id is not None:
        uid = str(unit_id or "").strip()
        prior_unit = str(
            prior_result.get("unit_id")
            or prior_result.get("repair_unit_id")
            or "",
        ).strip()
        if uid and prior_unit and uid != prior_unit:
            return None
    return resolve_bound_resume_agent_id(
        state,
        stored_agent_id=stored_agent_id,
        bound_run_uuid=bound_run_uuid,
    )


def schema_format_reminder(model: type[BaseModel]) -> str:
    """Секция промпта со схемой ответа без имени Python-класса.

    В SDK-промпт её подставляет ``ABCAgent.execute_validated`` из
    ``payload_model``. Роль не передаёт модель в текст hop. Схему не режет.
    """
    return response_schema_section(model)


class ABCAdapter(ABC):
    """Единая точка запуска: ``resume_agent_id`` или новый agent; ответ — ``AgentResult``."""

    async def run(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        resume_agent_id: str | None = None,
        payload_model: type[BaseModel] | None = None,
        format_instructions: str | None = None,
        validation_retry: bool = True,
        max_validation_attempts: int = 4,
        post_parse_validate: Callable[[BaseModel], None] | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        """Compatibility transport facade; validation is owned by ABCAgent."""
        from agents.abc_agent import ABCAgent

        read_scope = resolve_effective_read_scope(agent_key, state)
        return await ABCAgent.execute_validated(
            self, state, prompt=prompt, tools=tools,
            resume_agent_id=resume_agent_id, payload_model=payload_model,
            format_instructions=format_instructions, validation_retry=validation_retry,
            max_validation_attempts=max_validation_attempts,
            post_parse_validate=post_parse_validate,
            read_scope=read_scope,
            agent_key=agent_key,
        )

    @abstractmethod
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Callable[[str, Mapping[str, Any]], None] | None = None,
        read_scope: ReadScope | None = None,
    ) -> AgentResult:
        """Один hop к провайдеру (без parse/retry).

        ``resume_agent_id``: server id для resume или ``None`` для create.
        ``tool_call_guard``: проверка tool args; политику задаёт ``ABCAgent``.
        """
        ...
