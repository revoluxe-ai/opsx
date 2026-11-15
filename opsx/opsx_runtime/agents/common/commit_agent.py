"""Commit hop: LLM формирует сообщение, затем атомарный git add/commit.

Агент потребляет fix evidence предыдущего узла, индексирует объявленные файлы,
запрашивает у LLM subject/body на русском (через ``ABCAdapter``) и создаёт
локальный commit. Без adapter или при ошибке LLM — детерминированный русский шаблон.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.common.commit_message.generate import resolve_commit_message
from model_config.agent_roles import AgentRole
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repair.model_dispatch import resolve_model_for_role
from workflows.repair.schemas.agent import TokenUsage, WorkerReceipt
from workflows.repair.schemas.domain import RepairState


@dataclass(frozen=True)
class CommitResult:
    """Итог попытки commit hop."""

    commit_sha: str | None
    commit_subject: str | None
    commit_body: str | None
    commit_files: list[str]
    commit_status: str
    error: str | None = None


def as_state(value: RepairState | Mapping[str, Any]) -> RepairState:
    """Нормализовать mapping в ``RepairState``."""

    if isinstance(value, RepairState):
        return value
    return RepairState.model_validate(value)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Запустить git с захватом stdout/stderr."""
    return run_subprocess_utf8(["git", "-C", str(repo), *args])


def _commit_repo(state: RepairState) -> Path:
    """Корень git, где FIX применил изменения (как SDK ``workspace``)."""
    return Path(state.workspace).resolve()


def _usage_fields_for_state(
    state: RepairState,
    result: AgentResult | None,
) -> dict[str, Any]:
    """Поля usage/billing для ``WorkerReceipt`` из ответа adapter."""
    if result is None:
        return {
            "usage": None,
            "tokens_reported": None,
            "input_tokens_reported": None,
            "input_limit_exceeded": None,
            "cost": None,
            "cost_status": "unavailable",
            "agent_id": None,
            "run_id": None,
            "usage_source": "unavailable",
            "billing_error": None,
        }
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
    return {
        "usage": usage,
        "tokens_reported": usage.total_tokens if usage else None,
        "input_tokens_reported": observed_input,
        "input_limit_exceeded": exceeded,
        "cost": result.cost,
        "cost_status": result.cost_status,
        "agent_id": result.agent_id or None,
        "run_id": result.run_id or None,
        "usage_source": result.usage_source,
        "billing_error": result.billing_error,
    }


def _persist_receipt(output_dir: Path, receipt: WorkerReceipt) -> None:
    target = output_dir / "workers" / f"{receipt.hop}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _staged_diff_stat(repo: Path, files: list[str]) -> str:
    stat = _run_git(repo, "diff", "--cached", "--stat", "--", *files)
    if stat.returncode not in {0, 1}:
        return stat.stderr.strip()
    return stat.stdout.strip()


class CommitAgent:
    """Закоммитить изменения предыдущего workflow-узла."""

    def __init__(
        self,
        *,
        adapter: ABCAdapter | None = None,
        allow_empty: bool = True,
        max_validation_attempts: int = 3,
        receipt: Any | None = None,
    ):
        from check_workflow_receipt import CheckWorkflowReceipt

        self._adapter = adapter
        self.allow_empty = allow_empty
        self.max_validation_attempts = max_validation_attempts
        self._receipt = receipt if receipt is not None else CheckWorkflowReceipt.none()

    async def run(
        self,
        raw_state,
        *,
        commit_message_task: str | None = None,
    ) -> dict[str, Any]:
        """Создать локальный git commit для объявленных изменений FIX/apply.

        Args:
            raw_state: ``RepairState`` или mapping.
            commit_message_task: Опциональная задача для prompt commit message
                (перекрывает ``commit_message_task`` в state, если задана).
        """

        state = as_state(raw_state)
        if commit_message_task is not None:
            state = state.model_copy(update={"commit_message_task": commit_message_task})
        role = AgentRole.COMMIT
        slug = str(role).lower().replace("_worker", "").replace("_", "-")
        hop = f"{state.revision + 1:03d}-{slug}-{uuid.uuid4().hex[:6]}"
        execution_id = f"{state.mission_id}:{hop}"
        output_dir = Path(state.output_dir)
        resolved = resolve_model_for_role(state, role=role, hop=hop)
        model_id = resolved.model.name

        def _base_receipt(
            *,
            receipt_state: Literal["finished", "FAILED", "BLOCKED"],
            validated: bool,
            agent_result: AgentResult | None = None,
            error: str | None = None,
            payload: dict[str, Any] | None = None,
        ) -> WorkerReceipt:
            usage_kw = _usage_fields_for_state(state, agent_result)
            return WorkerReceipt(
                hop=hop,
                role=role,
                execution_id=execution_id,
                request_sha256=None,
                state=receipt_state,
                input_token_limit=state.max_agent_input_tokens,
                validated=validated,
                payload=payload,
                error=error,
                model_id=model_id,
                **usage_kw,
            )

        fix = state.fix
        if fix is None:
            return {
                "commit_status": "BLOCKED",
                "current_stage": "finish",
                "error": "commit requires fix evidence",
            }

        repo = _commit_repo(state)
        if not repo.exists():
            return {
                "commit_status": "BLOCKED",
                "current_stage": "finish",
                "error": f"repository path not found: {repo}",
            }

        files = list(dict.fromkeys(fix.files_changed))
        if not files:
            return {
                "commit_status": "BLOCKED",
                "current_stage": "finish",
                "error": "commit requires files_changed entries",
            }

        if not (resource_path(repo, ".git")).exists():
            _persist_receipt(
                output_dir,
                _base_receipt(receipt_state="finished", validated=True),
            )
            return {
                "commit_status": "SKIPPED_NO_GIT",
                "current_stage": "commit",
                "commit_files": files,
                "commit_sha": None,
                "commit_subject": None,
                "commit_body": None,
                "error": None,
            }

        add_result = await asyncio.to_thread(_run_git, repo, "add", "--", *files)
        if add_result.returncode:
            _persist_receipt(
                output_dir,
                _base_receipt(
                    receipt_state="FAILED",
                    validated=False,
                    error=add_result.stderr.strip() or "git add failed",
                ),
            )
            return {
                "commit_status": "FAILED",
                "current_stage": "finish",
                "commit_files": files,
                "commit_sha": None,
                "commit_subject": None,
                "commit_body": None,
                "error": add_result.stderr.strip() or "git add failed",
            }

        diff_stat = await asyncio.to_thread(_staged_diff_stat, repo, files)
        generation = await resolve_commit_message(
            state,
            adapter=self._adapter,
            hop=hop,
            staged_diff_stat=diff_stat or None,
            max_validation_attempts=self.max_validation_attempts,
        )
        subject = generation.subject
        body = generation.body
        message_payload = {
            "source": generation.source,
            "llm_error": generation.llm_error,
        }

        diff_result = await asyncio.to_thread(
            _run_git,
            repo,
            "diff",
            "--cached",
            "--quiet",
            "--",
            *files,
        )
        has_changes = diff_result.returncode == 1
        if diff_result.returncode not in {0, 1}:
            _persist_receipt(
                output_dir,
                _base_receipt(
                    receipt_state="FAILED",
                    validated=False,
                    agent_result=generation.agent_result,
                    error=diff_result.stderr.strip() or "git diff --cached failed",
                    payload=message_payload,
                ),
            )
            return {
                "commit_status": "FAILED",
                "current_stage": "finish",
                "commit_files": files,
                "commit_sha": None,
                "commit_subject": subject,
                "commit_body": body,
                "error": diff_result.stderr.strip() or "git diff --cached failed",
            }

        commit_args = ["commit", "-m", subject, "-m", body]
        if not has_changes and self.allow_empty:
            commit_args.insert(1, "--allow-empty")
        elif not has_changes:
            _persist_receipt(
                output_dir,
                _base_receipt(
                    receipt_state="finished",
                    validated=True,
                    agent_result=generation.agent_result,
                    payload=message_payload,
                ),
            )
            return {
                "commit_status": "SKIPPED_NO_DIFF",
                "current_stage": "commit",
                "commit_files": files,
                "commit_sha": None,
                "commit_subject": subject,
                "commit_body": body,
                "error": None,
            }

        commit_result = await asyncio.to_thread(_run_git, repo, *commit_args)
        if commit_result.returncode:
            _persist_receipt(
                output_dir,
                _base_receipt(
                    receipt_state="FAILED",
                    validated=False,
                    agent_result=generation.agent_result,
                    error=commit_result.stderr.strip() or "git commit failed",
                    payload=message_payload,
                ),
            )
            return {
                "commit_status": "FAILED",
                "current_stage": "finish",
                "commit_files": files,
                "commit_sha": None,
                "commit_subject": subject,
                "commit_body": body,
                "error": commit_result.stderr.strip() or "git commit failed",
            }

        sha_result = await asyncio.to_thread(_run_git, repo, "rev-parse", "HEAD")
        commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None
        _persist_receipt(
            output_dir,
            _base_receipt(
                receipt_state="finished",
                validated=True,
                agent_result=generation.agent_result,
                payload=message_payload,
            ),
        )
        return {
            "commit_status": "COMMITTED",
            "current_stage": "commit",
            "commit_files": files,
            "commit_sha": commit_sha,
            "commit_subject": subject,
            "commit_body": body,
            "error": None,
        }
