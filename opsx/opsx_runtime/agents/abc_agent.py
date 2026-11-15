"""Базовый агент графа: preflight → ``adapter.run`` → patch state.

После каждого adapter hop с ``stream_log`` адаптер сохраняет
``run.conversation_json()`` в ``*.conversation.json`` рядом с ``*.live.ndjson``
(см. ``persist_run_conversation`` в модуле стрима адаптера провайдера).

Политика resume при обрыве bridge-stream (``NetworkError`` /
``RemoteProtocolError`` / incomplete chunked read) задаётся здесь и
исполняется в ``CursorSDKAdapter._run_turn`` через
``is_cursor_bridge_stream_drop`` / ``log_cursor_bridge_stream_drop``.
Если run/agent на bridge уже нет — ``is_cursor_resume_target_gone`` и
``log_cursor_bridge_resume_impossible``, затем fresh launch+send того же hop.
"""

from __future__ import annotations

import inspect
import logging
import re
from abc import ABC
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

from agents.adapters.abc_adapter import (
    TERMINAL_SDK_STATUSES,
    ABCAdapter,
    AgentResult,
    AgentRunContractError,
    normalize_resume_agent_id,
    schema_format_reminder,
)
from agents.config import ReadScope, ReadScopeViolation
from agents.log_setup import ensure_opsx_stderr_logger
from agents.read_scope_turn import (
    READ_SCOPE_MARKER,
    build_tool_call_guard,
    read_scope_correction_prompt,
    role_uses_filesystem_tools,
)
from agents.response_schema import (
    SCHEMA_SECTION_MARKER,
    drop_embedded_response_schema,
    final_json_output_contract,
)
from agents.utils.response import parse_agent_json
from check_workflow_receipt import CheckWorkflowReceipt
from pydantic import BaseModel, ValidationError



_LOGGER = logging.getLogger(__name__)

ensure_opsx_stderr_logger(_LOGGER, level=logging.DEBUG)


def _format_exception_cause(exc: BaseException) -> str:
    cause = exc.__cause__
    if cause is None:
        return "none"
    return f"{type(cause).__name__}: {cause}"


_STREAM_DROP_TYPE_NAMES = frozenset(
    {
        "NetworkError",
        "RemoteProtocolError",
    },
)
_STREAM_DROP_MESSAGE_MARKERS = (
    "remoteprotocolerror",
    "incomplete chunked",
    "peer closed connection",
    "bridge request failed",
)


def _adapter_failure(exc: BaseException) -> dict:
    from agents.adapters.provider_registry import call_bootstrap_hook
    try:
        return call_bootstrap_hook("classify_failure", exc=exc)
    except RuntimeError as error:
        if "not registered" in str(error):
            return {}
        raise


def is_cursor_provider_access_denied(exc: BaseException) -> bool:
    """Compatibility name for the neutral adapter failure classifier."""
    return _adapter_failure(exc).get("category") == 'access_denied'



def is_cursor_bridge_launch_failure(exc: BaseException) -> bool:
    """Compatibility name for the neutral adapter failure classifier."""
    return _adapter_failure(exc).get("category") == 'bridge_unavailable'



def raise_cursor_provider_access_denied(*, state: Mapping[str, Any], exc: BaseException) -> None:
    """Stop the workflow on an adapter-classified permanent failure."""
    raise_cursor_infra_failure(exc, state=state)



def raise_cursor_infra_failure(exc: BaseException, *, state: Mapping[str, Any] | None = None) -> None:
    """Map adapter failure facts into the workflow's explicit stop outcome."""
    from workflows.stop_errors import VerifyStageStop
    descriptor = _adapter_failure(exc)
    if descriptor.get("category") not in {"access_denied", "bridge_unavailable", "stream_dropped"}:
        raise exc
    raise VerifyStageStop(
        descriptor["message"], code=descriptor["code"],
        stage=str((state or {}).get("stream_label") or (state or {}).get("agent_key") or "adapter"),
        evidence=str(exc), owner="infra", next_action=descriptor["next_action"],
    ) from exc



def is_cursor_bridge_stream_drop(exc: BaseException) -> bool:
    """Compatibility name for the neutral adapter failure classifier."""
    return _adapter_failure(exc).get("category") == 'stream_dropped'



def log_cursor_bridge_stream_drop(
    *,
    agent_name: str,
    run_id: str | None,
    agent_id: str | None = None,
    exc: BaseException,
    resume_attempt: int,
    max_resumes: int,
) -> None:
    """Structured error log перед SDK resume того же agent/run."""
    _LOGGER.error(
        (
            "cursor bridge stream drop: hop=%s run_id=%s agent_id=%s "
            "resume_attempt=%s/%s exception=%s: %s cause=%s"
        ),
        agent_name,
        run_id or "unknown",
        agent_id or "unknown",
        resume_attempt,
        max_resumes,
        type(exc).__name__,
        exc,
        _format_exception_cause(exc),
        exc_info=True,
    )


def is_cursor_resume_target_gone(exc: BaseException) -> bool:
    """Compatibility name for the neutral adapter failure classifier."""
    return _adapter_failure(exc).get("category") == 'target_gone'



def log_cursor_bridge_resume_impossible(
    *,
    agent_name: str,
    run_id: str | None,
    agent_id: str | None = None,
    exc: BaseException,
    resume_attempt: int,
    max_resumes: int,
) -> None:
    """ERROR: после stream drop resume того же run/agent на bridge невозможен."""
    _LOGGER.error(
        (
            "cursor bridge resume impossible: hop=%s run_id=%s agent_id=%s "
            "resume_attempt=%s/%s reason=%s: %s cause=%s"
        ),
        agent_name,
        run_id or "unknown",
        agent_id or "unknown",
        resume_attempt,
        max_resumes,
        type(exc).__name__,
        exc,
        _format_exception_cause(exc),
    )


class AgentValidationTerminalError(RuntimeError):
    """Non-retryable semantic/identity failure: never ask the model to forge authority."""


def _format_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return exc.json(indent=2, include_context=False)
    return f"{type(exc).__name__}: {exc}"


def _validation_causal_findings(exc: Exception) -> tuple[int, frozenset[str]]:
    """Stable unresolved findings; lower stage means strictly better validation.

    Stage 3: invalid JSON, 2: Pydantic schema, 1: semantic proof. We cannot
    infer progress from changed prose, line numbers or smaller 'got=N' values.
    """
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if any(error.get("type") == "json_invalid" for error in errors):
            return 3, frozenset({"JSON_INVALID"})
        findings = frozenset(
            f"{error.get('type', 'unknown')}@"
            + "/".join(str(part) for part in error.get("loc", ()))
            for error in errors
        )
        return 2, findings or frozenset({"SCHEMA_UNCLASSIFIED"})
    message = str(exc)
    if "JSON code fence" in message:
        return 3, frozenset({"JSON_CODE_FENCE"})
    if isinstance(exc, ValueError) and (
        "Agent JSON object not found" in message
        or "Unterminated JSON code fence" in message
    ):
        return 3, frozenset({"JSON_EXTRACT_FAILED"})
    # Do not treat altered line numbers or 'got=3' -> 'got=2' as progress:
    # these are the same unresolved causal proof finding.
    codes = re.findall(r"\b[A-Z][A-Z0-9_]{3,}\b", message)
    if codes and codes[0] == "SCENARIO_ROW_PROOF_INVALID" and len(codes) > 1:
        code = codes[1]
    else:
        code = codes[0] if codes else type(exc).__name__
    clause = re.search(r"\b(WHEN|THEN|AND)\b", message)
    return 1, frozenset({code + (":" + clause.group(1) if clause else "")})


def _validation_converges(
    previous: tuple[int, frozenset[str]],
    current: tuple[int, frozenset[str]],
) -> bool:
    """Есть прогресс для schema-retry, если findings изменились или stage улучшился.

    Повтор того же набора causal findings на том же stage — не прогресс
    (VALIDATION_NO_PROGRESS). Смена кода/состава finding — прогресс, пока не
    исчерпан ``validation_max_attempts``. Улучшение stage (3→2→1) тоже прогресс.
    Регрессия stage без смены findings — не прогресс.
    """
    old_stage, old_findings = previous
    new_stage, new_findings = current
    if new_stage < old_stage:
        return True
    if new_stage == old_stage:
        return new_findings != old_findings
    return False


def _validation_retry_prompt(
    *,
    validation_error: str,
    format_instructions: str,
    attempt: int,
    max_attempts: int,
) -> str:
    return (
        f"## SCHEMA RETRY ({attempt}/{max_attempts})\n\n"
        "Предыдущий ответ не прошёл валидацию (JSON-схема или инварианты контракта).\n\n"
        f"### Ошибки валидации\n\n{validation_error}\n\n"
        "### Требуемый формат\n\n"
        "Используй только INPUT CONTEXT из исходного промпта выше — не открывай файлы заново.\n"
        "Если в FROZEN CLAUSE LEDGER есть mapped_test_numbered_source — сверяй proof "
        "только с ним и с mapped_test_function_*_line; не меняй номера без нового чтения.\n"
        "Исправь только указанные ошибки ответа; не выдумывай proof.\n"
        "Верни только исправленный финальный JSON по указанной схеме.\n\n"
        f"{format_instructions}\n\n"
        f"{final_json_output_contract()}"
    )


def _hop_retry_continuation_prompt(
    *,
    attempt: int,
    max_attempts: int,
    reason: str,
    detail: str,
) -> str:
    """Узкое продолжение hop после RESOURCE_LIMIT / provider cancel (не полный re-hunt)."""
    if reason == "RESOURCE_LIMIT":
        return (
            f"## HOP CONTINUATION ({attempt}/{max_attempts}) — {reason}\n\n"
            f"{detail}\n\n"
            "Tool budget was exhausted. Do not re-scan the repo or guess paths.\n"
            "Read only from discoverability.priority_reads then discoverability.source_ids "
            "in INPUT CONTEXT (at most 12 reads total this attempt).\n"
            "If evidence is still insufficient after those reads, return BLOCKED/INCONCLUSIVE "
            "with cited paths you actually read.\n"
            "Return the JSON contract."
        )
    return (
        f"## HOP CONTINUATION ({attempt}/{max_attempts}) — {reason}\n\n"
        f"{detail}\n\n"
        "Do not restart broad codebase exploration. Use facts already gathered above.\n"
        "If pytest already returned exit_code!=0: at most **one** more targeted read, "
        "then call **edit** with a minimal hunk.\n"
        "For run_pytest use argument **node_id** (not nodeid).\n"
        "After edit, run pytest on the same node_id once and return the JSON contract."
    )


class ABCAgent(ABC):
    """Repair hop: ``run(state, *, prompt, payload)`` — текст и context задаёт caller."""

    role: ClassVar[Any]
    payload_model: ClassVar[type[BaseModel]]
    tools: ClassVar[Sequence[str]]
    # Общий лимит parse/semantic validation attempts; владелец — ABCAgent.
    validation_max_attempts: ClassVar[int] = 4
    # Resume того же Cursor agent/run после обрыва bridge-stream (не JSON retry).
    cursor_stream_resume_max_attempts: ClassVar[int] = 2
    # Коррекции промпта после ReadScopeViolation (не schema-retry).
    read_scope_max_corrections: ClassVar[int] = 2
    discovery_review_read_scope_max_corrections: ClassVar[int] = 10
    _DISCOVERY_REVIEW_AGENT_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"bugbot", "security_review"}
    )

    @classmethod
    def _read_scope_max_corrections_for_hop(
        cls,
        agent_key: str | None,
        *,
        discovery: bool,
    ) -> int:
        """Бюджет resume после ReadScopeViolation на discovery-hop."""
        if discovery and str(agent_key or "").strip() in cls._DISCOVERY_REVIEW_AGENT_KEYS:
            return cls.discovery_review_read_scope_max_corrections
        return cls.read_scope_max_corrections
    # Единственный resume после outside_restrict_allow на non-discovery hop (не discovery).
    non_discovery_outside_allow_max_corrections: ClassVar[int] = 1

    @staticmethod
    def _repo_root_from_state(state: Mapping[str, Any]) -> Path | None:
        from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root

        try:
            workspace = normalize_workspace_path(
                state.get("workspace"),
                state.get("repository_path"),
            )
            return resolve_opsx_repository_root(workspace)
        except (ValueError, OSError, TypeError):
            raw = str(state.get("repository_path") or state.get("workspace") or "").strip()
            if not raw:
                return None
            candidate = Path(raw)
            if candidate.is_dir():
                return candidate.resolve()
            return None

    @staticmethod
    def read_scope_prompt_section(
        scope: ReadScope,
        state: Mapping[str, Any] | None = None,
        *,
        repo_root: Path | None = None,
        hide_repo_root: bool = False,
    ) -> str:
        """Дописать в hop-промпт ALLOW/DENY из уже посчитанного ``ReadScope``.

        Списки берутся из scope (их задаёт ``agents.config``). Текст инструкции
        живёт здесь, не в конфиге разрешений. Абсолютный корень репозитория
        в промпт не попадает, когда ``hide_repo_root``.
        """
        _ = state
        deny_lines = "\n".join(f"- `{p}`" for p in scope.deny)
        allow = tuple(scope.restrict_allow or ())
        if allow:
            allow_lines = "\n".join(f"- `{p}`" for p in allow)
        else:
            allow_lines = "- (пусто — filesystem tools запрещены)"
        write_allow = tuple(scope.restrict_write_allow or ())
        write_block = ""
        if write_allow:
            write_lines = "\n".join(f"- `{p}`" for p in write_allow)
            write_block = (
                "\nWRITE ALLOW (edit/write only; read may use READ ALLOW выше):\n"
                f"{write_lines}\n"
            )
        if repo_root is not None and not hide_repo_root:
            root_text = str(repo_root.resolve())
            root_block = (
                f"REPO_ROOT (этот path ЗАПРЕЩЁН как path, target_directory и cwd): `{root_text}`\n"
                "Запрещены также пустой path, `.`, `./` и любой путь, равный REPO_ROOT "
                "или являющийся его родителем.\n"
                "Единственные легальные абсолютные пути — строго внутри одного префикса ALLOW, "
                f"то есть `{root_text}/<prefix>/...`, не сам REPO_ROOT и не каталог шире префикса.\n"
            )
        else:
            root_block = (
                "Абсолютный путь workspace в этот промпт не передаётся. "
                "Читай и правь только относительные пути из work_scope и ALLOW.\n"
            )
        return (
            f"{READ_SCOPE_MARKER} (ЖЁСТКО, нарушение обрывает hop)\n"
            f"{root_block}"
            "Инструменты read, grep, glob, ls, shell, edit, write, delete "
            "разрешены ТОЛЬКО по путям из списка ALLOW ниже.\n"
            "Вне ALLOW запрещено всё: REPO_ROOT, `openspec/` целиком, `opsx/` целиком, "
            "родительский каталог префикса, соседний change.\n"
            "Glob и grep с `**` от REPO_ROOT запрещены. Shell не делает cd в REPO_ROOT "
            "для обхода дерева.\n"
            "Контекст hop уже встроен в промпт выше. Не ищи входы обходом дерева.\n"
            "Если файла нет в промпте и его путь не попадает в ALLOW — не открывай его.\n"
            "Deny побеждает allow: путь внутри ALLOW всё равно запрещён, если совпал с DENY.\n\n"
            "ALLOW (read/shell/repo tools, repo-relative):\n"
            f"{allow_lines}\n"
            f"{write_block}\n"
            "DENY (запрещено даже при совпадении с ALLOW):\n"
            f"{deny_lines}\n"
        )

    @staticmethod
    def _append_read_scope_prompt(
        prompt: str,
        scope: ReadScope,
        state: Mapping[str, Any],
        *,
        repo_root: Path | None = None,
        hide_repo_root: bool = False,
    ) -> str:
        """Дописать блок каталогов, если маркера ещё нет."""
        if READ_SCOPE_MARKER in prompt:
            return prompt
        block = ABCAgent.read_scope_prompt_section(
            scope,
            state,
            repo_root=None if hide_repo_root else repo_root,
            hide_repo_root=hide_repo_root,
        )
        return f"{prompt.rstrip()}\n\n{block}"

    @staticmethod
    async def submit_turn(
        adapter: ABCAdapter,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        read_scope: ReadScope | None,
        resume_agent_id: str | None,
        payload_model: type[BaseModel] | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        """Единственная отправка в транспорт: scope в промпте, guard, resume при нарушении."""
        from agents.common.contract_prompts import prepend_contracts

        repo_root = ABCAgent._repo_root_from_state(state)
        resume = normalize_resume_agent_id(resume_agent_id)
        base_prompt = prepend_contracts(prompt, agent_key=agent_key)
        current_prompt = base_prompt
        corrections = 0
        recorded_outside_restrict_allow: ReadScopeViolation | None = None
        scope = read_scope
        capability_hop = state.get("capability_manifest") is not None
        discovery = not capability_hop and bool(
            {"grep", "glob", "shell", "ls"}.intersection(
                {str(name).strip().lower() for name in tools}
            )
        )
        hop_label = str(state.get("stream_label") or agent_key or "hop")
        budget_guard = None
        hop_estimate = 0
        budget_out = None
        from agents.budget.guard import BudgetExceeded as _BudgetExceeded
        from agents.budget.session import (
            begin_hop_budget,
            bind_intra_run_guard,
            finish_hop_budget,
        )

        while True:
            send_prompt = current_prompt
            if scope is not None and role_uses_filesystem_tools(tools):
                send_prompt = ABCAgent._append_read_scope_prompt(
                    current_prompt,
                    scope,
                    state,
                    repo_root=repo_root,
                    hide_repo_root=not discovery,
                )
            guard = build_tool_call_guard(scope, repo_root, agent_key=agent_key)
            turn_kwargs: dict[str, Any] = {
                "prompt": send_prompt,
                "tools": tools,
                "payload_model": payload_model,
                "resume_agent_id": resume,
            }
            if "tool_call_guard" in inspect.signature(
                adapter._run_turn
            ).parameters:
                turn_kwargs["tool_call_guard"] = guard
            sig = inspect.signature(adapter._run_turn)
            if "read_scope" in sig.parameters:
                turn_kwargs["read_scope"] = scope
            if "agent_key" in sig.parameters:
                turn_kwargs["agent_key"] = agent_key
            try:
                if budget_guard is None:
                    budget_guard, hop_estimate, budget_out = begin_hop_budget(
                        state,
                        role=agent_key,
                        hop=hop_label,
                        prompt=send_prompt,
                    )
                turn_state: Mapping[str, Any] = state
                if budget_guard is not None:
                    mutable = dict(state)
                    bind_intra_run_guard(mutable, budget_guard)
                    turn_state = mutable
                turn_result = await adapter._run_turn(turn_state, **turn_kwargs)
                if budget_guard is not None:
                    finish_hop_budget(
                        budget_guard,
                        budget_out,
                        hop=hop_label,
                        usage=turn_result.usage,
                        estimate=hop_estimate,
                    )
                merged_recorded = (
                    recorded_outside_restrict_allow
                    or turn_result.recorded_outside_restrict_allow
                )
                if merged_recorded is not None:
                    return replace(
                        turn_result,
                        recorded_outside_restrict_allow=merged_recorded,
                    )
                return turn_result
            except _BudgetExceeded as exc:
                raise AgentRunContractError(
                    "RESOURCE_LIMIT",
                    status="cancelled",
                    run_id="",
                    agent_id=str(resume or ""),
                    attempt=corrections + 1,
                    message=str(exc),
                ) from exc
            except ReadScopeViolation as exc:
                if exc.rule == "product_edit_destructive" and agent_key in {
                    "apply_scaffold",
                    "apply_red",
                    "apply_implement",
                    "apply_green",
                }:
                    corrections += 1
                    agent_id = str(exc.agent_id or "").strip()
                    if (
                        corrections > ABCAgent.read_scope_max_corrections
                        or not agent_id
                    ):
                        raise AgentRunContractError(
                            "PRODUCT_EDIT_DESTRUCTIVE",
                            status="cancelled",
                            run_id=str(exc.run_id or ""),
                            agent_id=agent_id,
                            attempt=corrections,
                            message=str(exc.command or exc),
                        ) from exc
                    from agents.implementation.product_file_edit_guard import (
                        product_edit_destructive_correction_prompt,
                    )

                    current_prompt = product_edit_destructive_correction_prompt(
                        exc,
                        base_prompt=base_prompt,
                    )
                    resume = None
                    continue
                if exc.rule in ("scaffold_receipt_schema", "slice_receipt_schema") and (
                    agent_key
                    in {"apply_scaffold", "apply_red", "apply_implement", "apply_green"}
                ):
                    corrections += 1
                    agent_id = str(exc.agent_id or "").strip()
                    if (
                        corrections > ABCAgent.read_scope_max_corrections
                        or not agent_id
                    ):
                        raise AgentRunContractError(
                            "SCAFFOLD_RECEIPT_WRITE_REJECTED",
                            status="cancelled",
                            run_id=str(exc.run_id or ""),
                            agent_id=agent_id,
                            attempt=corrections,
                            message="SCAFFOLD.json schema gate exhausted corrections",
                        ) from exc
                    from agents.implementation.slice_stage_receipt_edit_guard import (
                        slice_receipt_write_correction_prompt,
                    )

                    current_prompt = slice_receipt_write_correction_prompt(
                        exc,
                        base_prompt=base_prompt,
                    )
                    # Violation retires the bridge run; never resume that agent.
                    resume = None
                    continue
                if capability_hop:
                    raise AgentRunContractError(
                        "TOOL_SCOPE_VIOLATION",
                        status="cancelled",
                        run_id=str(exc.run_id or ""),
                        agent_id=str(exc.agent_id or ""),
                        attempt=1,
                        message="tool scope violation is an agent contract error",
                    ) from exc
                if not discovery:
                    if exc.rule == "outside_restrict_allow":
                        corrections += 1
                        agent_id = str(exc.agent_id or "").strip()
                        if (
                            corrections
                            > ABCAgent.non_discovery_outside_allow_max_corrections
                            or not agent_id
                        ):
                            from agents.verify.test_repair.scope_denial_handoff import (
                                is_test_repair_outside_allow_handoff,
                                synthetic_blocked_test_repair_agent_result,
                            )

                            if is_test_repair_outside_allow_handoff(agent_key, exc):
                                return synthetic_blocked_test_repair_agent_result(
                                    exc,
                                    repo_root=repo_root,
                                )
                            raise AgentRunContractError(
                                "TOOL_SCOPE_VIOLATION",
                                status="cancelled",
                                run_id=str(exc.run_id or ""),
                                agent_id=agent_id,
                                attempt=corrections,
                                message="tool scope violation is an agent contract error",
                            ) from exc
                        from agents.verify.test_repair.scope_denial_handoff import (
                            is_test_repair_outside_allow_handoff as _is_tr_scope_handoff,
                        )

                        if _is_tr_scope_handoff(agent_key, exc):
                            recorded_outside_restrict_allow = exc
                        current_prompt = read_scope_correction_prompt(
                            exc,
                            scope,
                            state,
                            base_prompt=base_prompt,
                            repo_root=repo_root,
                            non_discovery_outside_allow=True,
                        )
                        resume = None
                        continue
                    raise AgentRunContractError(
                        "TOOL_SCOPE_VIOLATION",
                        status="cancelled",
                        run_id=str(exc.run_id or ""),
                        agent_id=str(exc.agent_id or ""),
                        attempt=1,
                        message="tool scope violation is an agent contract error",
                    ) from exc
                corrections += 1
                agent_id = str(exc.agent_id or "").strip()
                scope_budget = ABCAgent._read_scope_max_corrections_for_hop(
                    agent_key,
                    discovery=discovery,
                )
                if (
                    scope is None
                    or corrections > scope_budget
                    or not agent_id
                ):
                    raise AgentRunContractError(
                        "READ_SCOPE_EXHAUSTED",
                        status="cancelled",
                        run_id=str(exc.run_id or ""),
                        agent_id=agent_id,
                        attempt=corrections,
                        message="read scope corrections exhausted",
                    ) from exc
                current_prompt = read_scope_correction_prompt(
                    exc, scope, state, base_prompt=base_prompt, repo_root=repo_root,
                )
                # Violation retires the bridge run; correction is one fresh hop.
                resume = None
            except BaseException as exc:
                if is_cursor_provider_access_denied(exc):
                    raise_cursor_provider_access_denied(state=state, exc=exc)
                raise

    @staticmethod
    async def execute_validated(
        adapter: ABCAdapter,
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
        read_scope: ReadScope | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        """Общий validation/retry lifecycle для всех schema-bound агентов.

        Args:
            resume_agent_id: Server id адаптера для resume или ``None`` (create).
            payload_model: При задании — parse JSON и опционально retry в той же session.

        Returns:
            ``AgentResult`` с полем ``agent_id`` для сохранения caller'ом в GraphState.
        """
        resume = normalize_resume_agent_id(resume_agent_id)
        if payload_model is None:
            return await ABCAgent.submit_turn(
                adapter,
                state,
                prompt=prompt,
                tools=tools,
                read_scope=read_scope,
                resume_agent_id=resume,
                payload_model=payload_model,
                agent_key=agent_key,
            )

        # Схему ответа собирает только ABCAgent из payload_model.
        # format_instructions роли не подмешивается: в ней оказывалось имя
        # Python-класса (title JSON Schema), и агент искал модель в репозитории.
        _ = format_instructions
        fmt = schema_format_reminder(payload_model)
        current_prompt = drop_embedded_response_schema(prompt.rstrip())
        if SCHEMA_SECTION_MARKER not in current_prompt:
            current_prompt = current_prompt.rstrip() + "\n\n" + fmt
        tail = final_json_output_contract()
        if not current_prompt.endswith(tail):
            current_prompt += "\n\n" + tail
        original_prompt = current_prompt

        if not validation_retry or max_validation_attempts < 1:
            return await ABCAgent.submit_turn(
                adapter,
                state,
                prompt=current_prompt,
                tools=tools,
                read_scope=read_scope,
                resume_agent_id=resume,
                payload_model=payload_model,
                agent_key=agent_key,
            )

        last_error = ""
        last_code = "SCHEMA_VALIDATION_FAILED"
        last_exc: BaseException | None = None
        last_result: AgentResult | None = None
        previous_findings: tuple[int, frozenset[str]] | None = None

        last_resource_detail = ""
        for attempt in range(1, max_validation_attempts + 1):
            try:
                result = await ABCAgent.submit_turn(
                    adapter,
                    state,
                    prompt=current_prompt,
                    tools=tools,
                    read_scope=read_scope,
                    resume_agent_id=resume,
                    payload_model=payload_model,
                    agent_key=agent_key,
                )
            except AgentRunContractError as exc:
                if exc.code == "RESOURCE_LIMIT" and attempt < max_validation_attempts:
                    last_resource_detail = str(exc.message or exc.code)
                    _LOGGER.warning(
                        "agent_response_failure code=RESOURCE_LIMIT run_id=%s "
                        "agent_id=%s attempt=%d/%d status=%s retry=hypothesis",
                        exc.run_id,
                        exc.agent_id,
                        attempt,
                        max_validation_attempts,
                        exc.status,
                    )
                    resume = normalize_resume_agent_id(exc.agent_id) or resume
                    current_prompt = (
                        f"{original_prompt.rstrip()}\n\n"
                        + _hop_retry_continuation_prompt(
                            attempt=attempt + 1,
                            max_attempts=max_validation_attempts,
                            reason="RESOURCE_LIMIT",
                            detail=last_resource_detail,
                        )
                    )
                    continue
                raise
            resume = normalize_resume_agent_id(result.agent_id)
            status = str(result.status).upper()
            status_raw = str(result.status).strip().lower()
            if status not in TERMINAL_SDK_STATUSES:
                if (
                    status_raw in ("cancelled", "error")
                    and attempt < max_validation_attempts
                ):
                    provider_code = (
                        "PROVIDER_CANCELLED"
                        if status_raw == "cancelled"
                        else "PROVIDER_ERROR"
                    )
                    detail = (
                        "Provider cancelled the run before a patch landed. "
                        "Continue the same hypothesis; do not re-scan the repo."
                        if status_raw == "cancelled"
                        else (
                            "model adapter run ended with provider error (e.g. HTTP 403). "
                            "Continue the same review; do not re-scan the repo."
                        )
                    )
                    _LOGGER.warning(
                        "agent_response_failure code=%s run_id=%s "
                        "agent_id=%s attempt=%d/%d status=%s usage=%s "
                        "retry=provider",
                        provider_code,
                        result.run_id,
                        result.agent_id,
                        attempt,
                        max_validation_attempts,
                        result.status,
                        result.usage,
                    )
                    # Provider terminal error invalidates bridge run — fresh hop only.
                    resume = None
                    current_prompt = (
                        f"{original_prompt.rstrip()}\n\n"
                        + _hop_retry_continuation_prompt(
                            attempt=attempt + 1,
                            max_attempts=max_validation_attempts,
                            reason=provider_code,
                            detail=detail,
                        )
                    )
                    continue
                _LOGGER.error(
                    "agent_response_failure code=PROVIDER_ERROR run_id=%s "
                    "agent_id=%s attempt=%d/%d status=%s usage=%s",
                    result.run_id, result.agent_id, attempt, max_validation_attempts,
                    result.status, result.usage,
                )
                raise AgentRunContractError(
                    "PROVIDER_ERROR",
                    status=str(result.status),
                    run_id=result.run_id,
                    agent_id=result.agent_id,
                    attempt=attempt,
                    message="Cursor run did not complete successfully",
                )
            # SDK FINISHED is a transport terminal, *not* proof of an answer.
            # A blank result has no JSON to repair. Re-sending the same
            # expensive analysis is neither a schema retry nor convergence.
            if not isinstance(result.text, str) or not result.text.strip():
                _LOGGER.error(
                    "agent_response_failure code=EMPTY_MODEL_RESPONSE run_id=%s "
                    "agent_id=%s attempt=%d/%d status=%s usage=%s retry=no",
                    result.run_id, result.agent_id, attempt, max_validation_attempts,
                    result.status, result.usage,
                )
                raise AgentRunContractError(
                    "EMPTY_MODEL_RESPONSE",
                    status=str(result.status),
                    run_id=result.run_id,
                    agent_id=result.agent_id,
                    attempt=attempt,
                    message="terminal SDK run had no answer text; automatic LLM retry forbidden",
                )
            try:
                parsed = parse_agent_json(result.text, payload_model)
                if post_parse_validate is not None:
                    post_parse_validate(parsed)
                return replace(
                    result,
                    parsed=parsed,
                    validation_attempts=attempt,
                )
            except (ValidationError, ValueError) as exc:
                last_exc = exc
                last_result = result
                last_error = _format_validation_error(exc)
                if isinstance(exc, ValidationError):
                    invalid_json = any(
                        item.get("type") == "json_invalid" for item in exc.errors()
                    )
                else:
                    invalid_json = "JSON code fence" in str(exc)
                last_code = "JSON_PARSE_FAILED" if invalid_json else "SCHEMA_VALIDATION_FAILED"
                findings = _validation_causal_findings(exc)
                if (
                    previous_findings is not None
                    and not _validation_converges(previous_findings, findings)
                ):
                    _LOGGER.error(
                        "agent_response_failure code=VALIDATION_NO_PROGRESS "
                        "run_id=%s agent_id=%s attempt=%d/%d "
                        "previous=%s current=%s retry=no",
                        result.run_id, result.agent_id, attempt, max_validation_attempts,
                        previous_findings, findings,
                    )
                    raise AgentRunContractError(
                        "VALIDATION_NO_PROGRESS",
                        status=str(result.status),
                        run_id=result.run_id,
                        agent_id=result.agent_id,
                        attempt=attempt,
                        message=(
                            "Validation did not converge: identical unresolved "
                            "findings repeated; model retry forbidden"
                        ),
                    ) from exc
                previous_findings = findings
                _LOGGER.warning(
                    "agent_response_failure code=%s run_id=%s agent_id=%s "
                    "attempt=%d/%d status=%s usage=%s retry=%s error_type=%s",
                    last_code, result.run_id, result.agent_id,
                    attempt, max_validation_attempts, result.status, result.usage,
                    "validation" if attempt < max_validation_attempts else "no",
                    type(exc).__name__,
                )
                if attempt >= max_validation_attempts:
                    break
                retry_block = _validation_retry_prompt(
                    validation_error=last_error,
                    format_instructions=fmt,
                    attempt=attempt + 1,
                    max_attempts=max_validation_attempts,
                )
                current_prompt = f"{original_prompt.rstrip()}\n\n{retry_block}"

        if last_exc is not None and last_result is not None:
            raise AgentRunContractError(
                last_code,
                status=str(last_result.status),
                run_id=last_result.run_id,
                agent_id=last_result.agent_id,
                attempt=max_validation_attempts,
                message="bounded non-empty response format correction exhausted",
            ) from last_exc
        detail = last_error or "unknown validation failure"
        raise RuntimeError(
            f"invalid agent JSON after {max_validation_attempts} attempt(s): {detail}",
        ) from None


    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "run" in cls.__dict__:
            return
        # Подкласс с кастомным ``run`` у предка (например test-only override preflight).
        for base in cls.__mro__[1:]:
            if base is ABCAgent:
                break
            if "run" in base.__dict__:
                return
        for name in ("role", "payload_model", "tools"):
            if name not in cls.__dict__ and getattr(cls, name, None) is None:
                raise TypeError(f"{cls.__name__} must define {name!r}")

    def __init__(
        self,
        adapter: ABCAdapter,
        receipt: CheckWorkflowReceipt | None = None,
        bug_journal: Any | None = None,
    ) -> None:
        self._adapter = adapter
        self._receipt = receipt if receipt is not None else CheckWorkflowReceipt.none()
        self._bug_journal = bug_journal

    def journal_observations(
        self,
        state: Any,
        payload: Any,
        receipt: Any,
        *,
        failed: bool = False,
    ) -> list[Any]:
        """Наблюдения для журнала багов. По умолчанию пусто.

        Returns:
            Список ``BugObservation``.
        """
        return []

    async def _flush_journal(
        self,
        state: Any,
        payload: Any,
        receipt: Any,
        *,
        failed: bool = False,
    ) -> None:
        from bug_log.context import active_journal

        journal = self._bug_journal or active_journal()
        if journal is None:
            return
        for observation in self.journal_observations(
            state, payload, receipt, failed=failed
        ):
            await journal.record(observation)

    def on_receipt_skip(self, state: Any) -> dict[str, Any] | None:
        """Ранний ответ, если ``self._receipt`` зашил файлы и они не изменились."""
        return None

    @staticmethod
    def conversation_path_for_stream_log(stream_log: str | Path) -> Path:
        """Путь артефакта полного диалога run рядом с NDJSON live-log.

        Args:
            stream_log: Путь к ``*.live.ndjson`` (или другому stream_log из state).

        Returns:
            Соседний ``*.conversation.json`` для ``GetRunConversation`` / анализа.
        """
        ndjson_path = Path(stream_log)
        target = ndjson_path.with_name(
            ndjson_path.name.replace(".live.ndjson", ".conversation.json")
        )
        if target.name == ndjson_path.name:
            target = ndjson_path.with_suffix(".conversation.json")
        return target

    @classmethod
    def conversation_path_from_state(
        cls,
        state: Mapping[str, Any],
        *,
        stream_log_key: str = "stream_log",
    ) -> Path | None:
        """Вычислить путь conversation-артефакта из GraphState / RepairState."""
        raw = state.get(stream_log_key)
        if not raw or not str(raw).strip():
            return None
        return cls.conversation_path_for_stream_log(str(raw))

    def preflight(self, state: Any) -> dict[str, Any] | None:
        """Ранний выход без вызова SDK; ``None`` — выполнять hop."""
        return None

    def on_success(
        self,
        state: Any,
        payload: BaseModel,
        receipt: Any,
    ) -> dict[str, Any]:
        """Собрать patch LangGraph после успешного JSON parse."""
        raise NotImplementedError

    def on_hop_failed(
        self,
        state: Any,
        execution: Any,
    ) -> dict[str, Any]:
        """Ответ при ошибке SDK или невалидном JSON."""
        from agents.repair.helpers import as_state, with_receipt

        state = as_state(state)
        return with_receipt(
            state,
            execution.receipt,
            status="BLOCKED",
            current_stage="finish",
            error=execution.error,
        )

    async def run(
        self,
        raw_state: Any,
        *,
        prompt: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """SDK hop: ``prompt`` и ``payload`` передаёт orchestration (graph / CLI)."""
        from agents.repair.execute_role import run_role
        from agents.repair.helpers import as_state

        state = as_state(raw_state)
        early = self.preflight(state)
        if early is not None:
            return early
        if self._receipt.agent_skip_hop(state):
            skipped = self.on_receipt_skip(state)
            if skipped is not None:
                return skipped

        execution = await run_role(
            state,
            adapter=self._adapter,
            role=self.role,
            payload_model=self.payload_model,
            tools=self.tools,
            payload=payload,
            prompt=prompt,
            max_validation_attempts=self.validation_max_attempts,
        )

        if execution.payload is None:
            failed = self.on_hop_failed(state, execution)
            await self._flush_journal(state, None, execution.receipt, failed=True)
            return failed

        patch = self.on_success(state, execution.payload, execution.receipt)
        await self._flush_journal(state, execution.payload, execution.receipt)
        return patch
