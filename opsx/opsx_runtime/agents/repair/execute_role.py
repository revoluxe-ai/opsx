"""Исполнение repair AI-hop: SDK, debug artifacts, Pydantic parse, receipt.

Агенты собирают prompt в ``prompt/get_prompt.py`` и вызывают ``run_role``.
Ответ модели — один JSON по schema роли (как Bugbot), без envelope repair.agent.v1.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel

from agents.adapters.abc_adapter import ABCAdapter, schema_format_reminder
from agents.repair.classifier_discoverability import enrich_classifier_payload
from agents.repair.prompt_wire import build_role_prompt
from agents.adapters.hop_model_selection import model_selection_for_hop as model_selection_for_sdk
from model_config.agent_roles import AgentRole, coerce_agent_role
from agents.budget.guard import BudgetExceeded
from agents.budget.session import (
    begin_hop_budget,
    bind_intra_run_guard,
    finish_hop_budget,
)
from workflows.repair.model_dispatch import resolve_model_for_role
from workflows.repair.progress import emit_repair_progress
from workflows.repair.schemas import RepairState, TokenUsage, WorkerReceipt

_STAGE_RUNNING_DETAIL = {
    AgentRole.CLASSIFIER: "Анализируем описание и определяем тип проблемы",
    AgentRole.RED_TEST_WORKER: "Создаём тест, который воспроизводит ошибку",
    AgentRole.IMPLEMENTATION_EXECUTOR: "Вносим минимальное исправление в код",
    AgentRole.GREEN_VERIFIER: "Проверяем исправление и связанные регрессии",
    AgentRole.INVARIANT_CAPTURE_WORKER: "Формулируем защиту от повторения ошибки",
    AgentRole.BOUNDED_HUNTER: "Ищем похожие нарушения в соседнем коде",
    AgentRole.RELATION_CLASSIFIER: "Проверяем связь найденной проблемы с исходным багом",
    AgentRole.FAN_IN_ASSEMBLER: "Собираем итог по исправлению и проверкам",
}


@dataclass(frozen=True)
class RoleExecution:
    """Результат одного AI-hop: typed payload и сохранённый receipt."""

    payload: BaseModel | None
    receipt: WorkerReceipt
    error: str | None = None


@dataclass(frozen=True)
class HopMeta:
    """Идентификаторы hop для receipt и debug-файлов."""

    hop: str
    execution_id: str


@dataclass(frozen=True)
class HopArtifacts:
    """Пути и хеши отладочных файлов одного hop (относительно ``output_dir``)."""

    prompt_artifact: str
    prompt_sha256: str
    response_raw_artifact: str | None = None
    response_raw_sha256: str | None = None


def _hop(role: AgentRole, revision: int) -> str:
    slug = str(role).lower().replace("_worker", "").replace("_", "-")
    return f"{revision + 1:03d}-{slug}-{uuid.uuid4().hex[:6]}"


def make_hop(state: RepairState, role: AgentRole) -> HopMeta:
    """Сформировать hop и execution_id для receipt и workers/."""
    hop = _hop(role, state.revision)
    return HopMeta(hop=hop, execution_id=f"{state.mission_id}:{hop}")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _persist_hop_artifacts(
    state: RepairState,
    hop: str,
    *,
    prompt: str,
    context: dict[str, Any],
    meta: HopMeta,
    raw_text: str | None = None,
) -> HopArtifacts:
    """Записать prompt, context и сырой SDK-ответ для отладки."""
    workers = Path(state.output_dir) / "workers"
    workers.mkdir(parents=True, exist_ok=True)

    prompt_name = f"{hop}.prompt.txt"
    (workers / prompt_name).write_text(prompt, encoding="utf-8")
    prompt_sha = _sha256_text(prompt)

    context_name = f"{hop}.context.json"
    (workers / context_name).write_text(
        json.dumps(
            {
                "hop": meta.hop,
                "execution_id": meta.execution_id,
                "context": context,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    raw_artifact: str | None = None
    raw_sha: str | None = None
    if raw_text is not None:
        raw_name = f"{hop}.response.raw.txt"
        (workers / raw_name).write_text(raw_text, encoding="utf-8")
        raw_artifact = f"workers/{raw_name}"
        raw_sha = _sha256_text(raw_text)

    return HopArtifacts(
        prompt_artifact=f"workers/{prompt_name}",
        prompt_sha256=prompt_sha,
        response_raw_artifact=raw_artifact,
        response_raw_sha256=raw_sha,
    )


def _artifact_receipt_fields(artifacts: HopArtifacts | None) -> dict[str, Any]:
    if artifacts is None:
        return {}
    return {
        "prompt_artifact": artifacts.prompt_artifact,
        "prompt_sha256": artifacts.prompt_sha256,
        "response_raw_artifact": artifacts.response_raw_artifact,
        "response_raw_sha256": artifacts.response_raw_sha256,
    }


def _persist_receipt(state: RepairState, receipt: WorkerReceipt) -> None:
    bug_id = state.active.bug_id if state.active is not None else None
    if bug_id and not receipt.bug_id:
        receipt = receipt.model_copy(update={"bug_id": bug_id})
    output_dir = Path(state.output_dir)
    workers = output_dir / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    target = workers / f"{receipt.hop}.json"
    target.write_text(
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


async def run_role(
    state: RepairState,
    *,
    adapter: ABCAdapter,
    role: AgentRole,
    payload_model: type[BaseModel],
    tools: Sequence[str],
    payload: dict[str, Any],
    instructions: str = "",
    prompt: str | None = None,
    hop_meta: HopMeta | None = None,
    validation_retry: bool = True,
    max_validation_attempts: int = 3,
) -> RoleExecution:
    """Выполнить AI-hop: ``adapter.run`` с JSON validation retry."""
    agent_role = coerce_agent_role(role)
    stage = str(agent_role.value)
    meta = hop_meta or make_hop(state, agent_role)
    hop = meta.hop
    context_payload = dict(payload)
    if agent_role == AgentRole.CLASSIFIER:
        context_payload = enrich_classifier_payload(state, context_payload)
    if prompt is None:
        prompt = build_role_prompt(
            state,
            role=agent_role,
            role_instructions=instructions,
            payload_model=payload_model,
            context=context_payload,
        )

    resolved_model = resolve_model_for_role(state, role=agent_role, hop=hop)
    model_receipt_fields = {
        "model_id": resolved_model.model.name,
    }

    workers_dir = Path(state.output_dir) / "workers"
    stream_log = workers_dir / f"{hop}.live.ndjson"

    adapter_state = {
        "workspace": state.workspace,
        "repository_path": state.repository_path,
        "isolated_workspace": state.isolated_workspace,
        "model": model_selection_for_sdk(resolved_model),
        "stream_log": str(stream_log),
        "stream_label": hop,
        "output_dir": state.output_dir,
        "mission_id": state.mission_id,
    }

    artifacts: HopArtifacts | None = None
    budget_guard, hop_estimate, _budget_out = begin_hop_budget(
        {"output_dir": state.output_dir, "mission_id": state.mission_id},
        role=str(agent_role.value),
        hop=hop,
        prompt=prompt,
    )
    bind_intra_run_guard(adapter_state, budget_guard)

    try:
        artifacts = _persist_hop_artifacts(
            state,
            hop,
            prompt=prompt,
            context=context_payload,
            meta=meta,
            raw_text=None,
        )
        try:
            result = await adapter.run(
                adapter_state,
                prompt=prompt,
                tools=tools,
                payload_model=payload_model,
                format_instructions=schema_format_reminder(payload_model),
                validation_retry=validation_retry,
                max_validation_attempts=max_validation_attempts,
                agent_key=str(agent_role.value),
            )
        except BudgetExceeded as exc:
            finish_hop_budget(
                budget_guard,
                Path(state.output_dir),
                hop=hop,
                usage=None,
                estimate=hop_estimate,
            )
            error = f"RESOURCE_LIMIT: {exc}"
            receipt = WorkerReceipt(
                hop=hop,
                role=agent_role,
                execution_id=meta.execution_id,
                state="BLOCKED",
                input_token_limit=state.max_agent_input_tokens,
                budget_stop_reason="RESOURCE_LIMIT",
                error=error,
                validated=False,
                **_artifact_receipt_fields(artifacts),
                **model_receipt_fields,
            )
            _persist_receipt(state, receipt)
            return RoleExecution(None, receipt, error)
        artifacts = _persist_hop_artifacts(
            state,
            hop,
            prompt=prompt,
            context=context_payload,
            meta=meta,
            raw_text=result.text or "",
        )
    except Exception as exc:
        from workflows.stop_errors import OpsxStageStop, format_stage_stop

        artifacts = _persist_hop_artifacts(
            state,
            hop,
            prompt=prompt,
            context=context_payload,
            meta=meta,
            raw_text=None,
        )
        if isinstance(exc, OpsxStageStop):
            error = format_stage_stop(exc).strip()
        else:
            error = f"{type(exc).__name__}: {exc}"
        receipt = WorkerReceipt(
            hop=hop,
            role=agent_role,
            execution_id=meta.execution_id,
            state="FAILED",
            input_token_limit=state.max_agent_input_tokens,
            error=error,
            validated=False,
            **_artifact_receipt_fields(artifacts),
            **model_receipt_fields,
        )
        _persist_receipt(state, receipt)
        emit_repair_progress(
            event="stage",
            stage=stage,
            state="failed",
            detail=receipt.error or "Этап завершился ошибкой",
            data={"hop": hop},
        )
        return RoleExecution(
            payload=None,
            receipt=receipt,
            error=receipt.error,
        )

    finish_hop_budget(
        budget_guard,
        Path(state.output_dir),
        hop=hop,
        usage=result.usage,
        estimate=hop_estimate,
    )
    usage = (
        TokenUsage.model_validate(result.usage)
        if result.usage is not None
        else None
    )
    observed_input = usage.observed_input_tokens() if usage else None
    exceeded = (
        None
        if observed_input is None
        else observed_input > state.max_agent_input_tokens
    )
    sdk_status = str(result.status).upper()
    terminal_ok = sdk_status in {
        "COMPLETED",
        "SUCCEEDED",
        "SUCCESS",
        "FINISHED",
    }

    if exceeded is True:
        error = (
            "agent input-token limit exceeded: "
            f"{observed_input}>{state.max_agent_input_tokens}"
        )
        receipt = WorkerReceipt(
            hop=hop,
            role=agent_role,
            execution_id=meta.execution_id,
            state="BLOCKED",
            input_token_limit=state.max_agent_input_tokens,
            usage=usage,
            tokens_reported=usage.total_tokens if usage else None,
            input_tokens_reported=observed_input,
            input_limit_exceeded=True,
            cost=result.cost,
            cost_status=result.cost_status,
            agent_id=result.agent_id,
            run_id=result.run_id,
            usage_source=result.usage_source,
            billing_error=result.billing_error,
            error=error,
            validated=False,
            **_artifact_receipt_fields(artifacts),
            **model_receipt_fields,
        )
        _persist_receipt(state, receipt)
        emit_repair_progress(
            event="stage",
            stage=stage,
            state="blocked",
            detail=error,
            data={"hop": hop},
        )
        return RoleExecution(None, receipt, error)

    if not terminal_ok:
        error = f"Cursor run did not finish successfully: {result.status}"
        receipt = WorkerReceipt(
            hop=hop,
            role=agent_role,
            execution_id=meta.execution_id,
            state="FAILED",
            input_token_limit=state.max_agent_input_tokens,
            usage=usage,
            tokens_reported=usage.total_tokens if usage else None,
            input_tokens_reported=observed_input,
            input_limit_exceeded=exceeded,
            cost=result.cost,
            cost_status=result.cost_status,
            agent_id=result.agent_id,
            run_id=result.run_id,
            usage_source=result.usage_source,
            billing_error=result.billing_error,
            error=error,
            validated=False,
            **_artifact_receipt_fields(artifacts),
            **model_receipt_fields,
        )
        _persist_receipt(state, receipt)
        emit_repair_progress(
            event="stage",
            stage=stage,
            state="failed",
            detail=error,
            data={"hop": hop},
        )
        return RoleExecution(None, receipt, error)

    typed_payload = result.parsed
    if typed_payload is None:
        error = "adapter.run returned no parsed payload"
        receipt = WorkerReceipt(
            hop=hop,
            role=agent_role,
            execution_id=meta.execution_id,
            state="FAILED",
            input_token_limit=state.max_agent_input_tokens,
            usage=usage,
            tokens_reported=usage.total_tokens if usage else None,
            input_tokens_reported=observed_input,
            input_limit_exceeded=exceeded,
            cost=result.cost,
            cost_status=result.cost_status,
            agent_id=result.agent_id,
            run_id=result.run_id,
            usage_source=result.usage_source,
            billing_error=result.billing_error,
            error=error,
            validated=False,
            **_artifact_receipt_fields(artifacts),
            **model_receipt_fields,
        )
        _persist_receipt(state, receipt)
        emit_repair_progress(
            event="stage",
            stage=stage,
            state="failed",
            detail=error,
            data={"hop": hop},
        )
        return RoleExecution(None, receipt, error)

    receipt = WorkerReceipt(
        hop=hop,
        role=agent_role,
        execution_id=meta.execution_id,
        state="finished",
        input_token_limit=state.max_agent_input_tokens,
        usage=usage,
        tokens_reported=usage.total_tokens if usage else None,
        input_tokens_reported=observed_input,
        input_limit_exceeded=exceeded,
        cost=result.cost,
        cost_status=result.cost_status,
        agent_id=result.agent_id,
        run_id=result.run_id,
        usage_source=result.usage_source,
        billing_error=result.billing_error,
        validated=True,
        payload=typed_payload.model_dump(mode="json"),
        error=None,
        **_artifact_receipt_fields(artifacts),
        **model_receipt_fields,
    )
    _persist_receipt(state, receipt)
    payload_json = typed_payload.model_dump(mode="json")
    payload_status = str(payload_json.get("status") or "finished")
    emit_repair_progress(
        event="stage",
        stage=stage,
        state="completed",
        detail=payload_status,
        data={
            "hop": hop,
            "status": payload_status,
            "summary": payload_json.get("summary") or payload_json.get("notes"),
            "files_changed": payload_json.get("files_changed") or [],
            "test_id": payload_json.get("test_id"),
        },
    )
    return RoleExecution(
        payload=typed_payload,
        receipt=receipt,
        error=None,
    )
