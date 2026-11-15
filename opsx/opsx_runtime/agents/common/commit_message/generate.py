"""Вызов LLM для генерации subject/body commit (с fallback)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents.adapters.abc_adapter import ABCAdapter, AgentResult, schema_format_reminder
from agents.common.commit_message.input_pack import (
    build_commit_message_input_pack,
    pack_json,
)
from agents.common.commit_message.prompt.get_prompt import get_prompt
from agents.common.commit_message.schemas import CommitMessageResult
from agents.repair.helpers import READ_ONLY
from model_config.agent_roles import AgentRole
from agents.adapters.hop_model_selection import model_selection_for_hop
from workflows.repair.model_dispatch import resolve_model_for_role
from workflows.repair.schemas import RepairState


@dataclass(frozen=True)
class CommitMessageGeneration:
    """Итог генерации сообщения коммита."""

    subject: str
    body: str
    source: Literal["llm", "fallback"]
    agent_result: AgentResult | None = None
    llm_error: str | None = None


def commit_message_fallback(state: RepairState) -> tuple[str, str]:
    """Детерминированный русский шаблон, если LLM недоступен или не прошёл валидацию."""
    fix = state.fix
    if fix is None:
        return "chore(repair): нет fix evidence", "Fix evidence не передан."

    subject = f"fix: {fix.summary}".strip()
    if len(subject) > 72:
        subject = subject[:69] + "..."

    files = ", ".join(fix.files_changed) if fix.files_changed else "нет"
    reason = fix.notes or fix.summary
    body = "\n".join(
        [
            "Что:",
            f"- файлы: {files}",
            "",
            "Зачем:",
            f"- {reason}",
            "",
            "Источник:",
            "- предыдущий hop: IMPLEMENTATION_EXECUTOR / apply",
        ]
    )
    return subject, body


def _normalize_subject(subject: str) -> str:
    line = " ".join(subject.strip().splitlines())
    if len(line) > 72:
        return line[:69] + "..."
    return line


async def generate_commit_message(
    state: RepairState,
    *,
    adapter: ABCAdapter,
    hop: str,
    staged_diff_stat: str | None,
    max_validation_attempts: int = 3,
) -> CommitMessageGeneration:
    """Сгенерировать subject/body через ``ABCAdapter``; при ошибке — fallback."""
    package = build_commit_message_input_pack(state, staged_diff_stat=staged_diff_stat)
    prompt = get_prompt(
        input_pack_json=pack_json(package),
        commit_message_task=state.commit_message_task,
    )
    resolved = resolve_model_for_role(state, role=AgentRole.COMMIT, hop=hop)
    from pathlib import Path

    stream_log = Path(state.output_dir) / "workers" / f"{hop}.live.ndjson"
    adapter_state = {
        "workspace": state.workspace,
        "repository_path": state.repository_path,
        "isolated_workspace": state.isolated_workspace,
        "model": model_selection_for_hop(resolved),
        "stream_log": str(stream_log),
        "stream_label": hop,
    }
    try:
        result = await adapter.run(
            adapter_state,
            prompt=prompt,
            tools=READ_ONLY,
            payload_model=CommitMessageResult,
            format_instructions=schema_format_reminder(CommitMessageResult),
            max_validation_attempts=max_validation_attempts,
        )
    except Exception as exc:
        subject, body = commit_message_fallback(state)
        return CommitMessageGeneration(
            subject=subject,
            body=body,
            source="fallback",
            agent_result=None,
            llm_error=f"{type(exc).__name__}: {exc}",
        )

    parsed = result.parsed
    if not isinstance(parsed, CommitMessageResult):
        subject, body = commit_message_fallback(state)
        return CommitMessageGeneration(
            subject=subject,
            body=body,
            source="fallback",
            agent_result=result,
            llm_error="LLM response did not produce valid CommitMessageResult",
        )

    return CommitMessageGeneration(
        subject=_normalize_subject(parsed.subject),
        body=parsed.body.strip(),
        source="llm",
        agent_result=result,
        llm_error=None,
    )


async def resolve_commit_message(
    state: RepairState,
    *,
    adapter: ABCAdapter | None,
    hop: str,
    staged_diff_stat: str | None,
    max_validation_attempts: int = 3,
) -> CommitMessageGeneration:
    """LLM при наличии adapter, иначе сразу fallback."""
    if adapter is None:
        subject, body = commit_message_fallback(state)
        return CommitMessageGeneration(subject=subject, body=body, source="fallback")
    return await generate_commit_message(
        state,
        adapter=adapter,
        hop=hop,
        staged_diff_stat=staged_diff_stat,
        max_validation_attempts=max_validation_attempts,
    )
