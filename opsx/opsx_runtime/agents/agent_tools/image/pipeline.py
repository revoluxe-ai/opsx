"""Оркестрация execute_image_task: agent → technical → visual → artifacts."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path
from typing import Any

from agents.agent_tools.image.acceptance import (
    idempotent_accepted,
    prior_accepted_revision,
    publish_image_result,
    publish_receipt,
    repair_requires_new_bytes,
)
from agents.agent_tools.image.repair_budget import (
    RepairBudgetPolicy,
    budget_exhausted,
    ensure_repair_state,
    exhaustion_reasons,
    mark_exhausted,
    record_attempt,
    save_repair_state,
    visual_counts_as_progress,
)
from agents.agent_tools.image.child_tools import (
    ImageAssetChildToolRuntime,
    build_image_child_custom_tools,
)
from agents.agent_tools.image.image_asset_agent import ImageAssetAgent
from agents.agent_tools.image.ports.cursor_generation import CursorProviderImageGenerationPort
from agents.agent_tools.image.ports.model_input import ImageModelInputPort
from agents.agent_tools.image.ports.render import ImageRenderPort
from agents.agent_tools.image.resolver import ImageTaskResolver, ImageTaskResolverError
from agents.agent_tools.image.schemas import (
    ImageAssetHopInput,
    ImageAssetHopMode,
    ImageAssetTaskResult,
    ImageDesignTask,
    VisualFinding,
)
from agents.agent_tools.image.technical_gate import run_technical_gate
from agents.agent_tools.image.visual_review import ImageVisualReviewAgent
from agents.agent_tools.parent_context import AgentToolParentContext


async def _deterministic_compose(
    repo: Path,
    task: ImageDesignTask,
) -> None:
    """Тестовый/backup путь: compose без LLM, если adapter вернул BLOCKED."""
    gen = CursorProviderImageGenerationPort(allow_compose_fallback=True)
    runtime = ImageAssetChildToolRuntime(
        repo_root=repo,
        task=task,
        generation=gen,
        model_input=ImageModelInputPort(repo_root=repo),
        render=ImageRenderPort(repo_root=repo),
    )
    tools = build_image_child_custom_tools(runtime)
    await tools["image_compose"].execute({}, None)


class ImageTaskPipeline:
    """Связка resolver, ImageAssetAgent, gates."""

    def __init__(
        self,
        *,
        image_agent: ImageAssetAgent,
        visual_reviewer: ImageVisualReviewAgent | None = None,
        allow_deterministic_fallback: bool = False,
    ) -> None:
        self._image_agent = image_agent
        self._visual = visual_reviewer or ImageVisualReviewAgent(use_llm=False)
        self._allow_deterministic_fallback = allow_deterministic_fallback

    async def execute(
        self,
        parent_ctx: AgentToolParentContext,
        *,
        task_id: str,
        mode: str = "create",
        visual_findings: list[dict[str, Any]] | None = None,
        model: dict[str, Any] | None = None,
        workspace: str | None = None,
    ) -> ImageAssetTaskResult:
        repo = Path(parent_ctx.repository_path).resolve()
        resolver = ImageTaskResolver(repo_root=repo)
        try:
            task = resolver.resolve(
                parent_ctx.change_id,
                task_id,
                slice_id=parent_ctx.slice_id,
            )
        except ImageTaskResolverError as exc:
            return ImageAssetTaskResult(
                status="BLOCKED",
                task_id=task_id,
                code=exc.code,
                message=exc.message,
            )

        reused = idempotent_accepted(repo, parent_ctx.change_id, task)
        if reused and mode == "create":
            return reused

        policy = RepairBudgetPolicy(
            max_attempts=int(task.repair_max_attempts or RepairBudgetPolicy().max_attempts),
            deadline_seconds=int(
                task.repair_deadline_seconds or RepairBudgetPolicy().deadline_seconds,
            ),
        )
        repair_state = ensure_repair_state(
            repo,
            parent_ctx.change_id,
            task_id,
            policy,
        )
        exhausted, exhaust_code = budget_exhausted(repair_state)
        if exhausted:
            reasons = exhaustion_reasons(repair_state)
            repair_state = mark_exhausted(repair_state, exhaust_code or "IMAGE_REPAIR_BUDGET_EXHAUSTED", reasons)
            save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)
            return ImageAssetTaskResult(
                status="EXHAUSTED",
                task_id=task_id,
                code=exhaust_code,
                message="; ".join(reasons),
                output_path=task.output_path,
                extra={
                    "attempts_used": repair_state.get("attempts_used"),
                    "max_attempts": repair_state.get("max_attempts"),
                    "exhaust_reasons": reasons,
                },
            )

        findings_models = [
            VisualFinding.model_validate(f)
            for f in (visual_findings or [])
            if isinstance(f, dict)
        ]
        hop_mode = ImageAssetHopMode.REPAIR if mode == "repair" else ImageAssetHopMode.CREATE
        prior_rev = prior_accepted_revision(repo, parent_ctx.change_id, task.task_id)

        hop_input = ImageAssetHopInput(
            task=task,
            mode=hop_mode,
            visual_findings=findings_models,
            prior_output_revision=prior_rev,
            allowlisted_output=task.output_path,
        )

        agent_answer = await self._image_agent.invoke(
            parent_ctx,
            hop_input,
            model=model,
            workspace=workspace,
        )
        if agent_answer.status != "DONE":
            if self._allow_deterministic_fallback:
                await _deterministic_compose(repo, task)
            else:
                return ImageAssetTaskResult(
                    status="BLOCKED",
                    task_id=task_id,
                    code="AGENT_HOP_INCOMPLETE",
                    message=agent_answer.notes or agent_answer.status,
                )
        out_path = resource_path(repo, task.output_path)
        if not out_path.is_file():
            if self._allow_deterministic_fallback:
                await _deterministic_compose(repo, task)
            else:
                return ImageAssetTaskResult(
                    status="BLOCKED",
                    task_id=task_id,
                    code="OUTPUT_NOT_PRODUCED",
                    message="ImageAssetAgent did not write output_path",
                )

        technical = await asyncio.to_thread(run_technical_gate, repo_root=repo, task=task)
        if not technical.ok:
            repair_state = record_attempt(
                repair_state,
                mode=mode,
                output_revision=technical.output_revision,
                visual=None,
                made_progress=False,
                note="technical_fail",
            )
            save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)
            result = ImageAssetTaskResult(
                status="FAIL",
                task_id=task_id,
                technical=technical,
                output_path=task.output_path,
                code="TECHNICAL_FAIL",
                extra={"attempts_used": repair_state.get("attempts_used")},
            )
            publish_image_result(repo, parent_ctx.change_id, result)
            return result

        prior_codes_raw = repair_state.get("last_finding_codes")
        prior_codes = (
            set(prior_codes_raw) if isinstance(prior_codes_raw, list) else None
        )

        if hop_mode == ImageAssetHopMode.REPAIR and repair_requires_new_bytes(
            prior_rev,
            technical,
        ):
            repair_state = record_attempt(
                repair_state,
                mode=mode,
                output_revision=technical.output_revision,
                visual=None,
                made_progress=False,
                note="revision unchanged",
            )
            save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)
            exhausted_after, code_after = budget_exhausted(repair_state)
            if exhausted_after:
                reasons = exhaustion_reasons(repair_state)
                repair_state = mark_exhausted(
                    repair_state,
                    code_after or "IMAGE_REPAIR_BUDGET_EXHAUSTED",
                    reasons,
                )
                save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)
                return ImageAssetTaskResult(
                    status="EXHAUSTED",
                    task_id=task_id,
                    technical=technical,
                    code=code_after,
                    message="; ".join(reasons),
                    output_path=task.output_path,
                    extra={"exhaust_reasons": reasons},
                )
            return ImageAssetTaskResult(
                status="BLOCKED",
                task_id=task_id,
                technical=technical,
                code="IMAGE_REPAIR_REQUIRED",
                message="output_revision unchanged after repair (not progress)",
                output_path=task.output_path,
                extra={
                    "attempts_used": repair_state.get("attempts_used"),
                    "made_progress": False,
                },
            )

        visual = await self._visual.review(task, repo_root=repo)
        made_progress = visual_counts_as_progress(
            prior_finding_codes=prior_codes,
            verdict=visual,
        )
        repair_state = record_attempt(
            repair_state,
            mode=mode,
            output_revision=technical.output_revision,
            visual=visual,
            made_progress=made_progress,
            note="" if made_progress else "no_visual_progress",
        )
        if visual.status != "PASS":
            repair_state["last_finding_codes"] = sorted({f.code for f in visual.findings})
        save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)

        exhausted_after, code_after = budget_exhausted(repair_state)
        if visual.status != "PASS" and exhausted_after:
            reasons = exhaustion_reasons(repair_state)
            repair_state = mark_exhausted(
                repair_state,
                code_after or "IMAGE_REPAIR_BUDGET_EXHAUSTED",
                reasons,
            )
            save_repair_state(repo, parent_ctx.change_id, task_id, repair_state)
            return ImageAssetTaskResult(
                status="EXHAUSTED",
                task_id=task_id,
                technical=technical,
                visual=visual,
                code=code_after,
                message="; ".join(reasons),
                output_path=task.output_path,
                extra={
                    "attempts_used": repair_state.get("attempts_used"),
                    "made_progress": made_progress,
                    "exhaust_reasons": reasons,
                },
            )

        status = "ACCEPTED" if visual.status == "PASS" else "VISUAL_FAIL"
        result = ImageAssetTaskResult(
            status=status,
            task_id=task_id,
            technical=technical,
            visual=visual,
            output_path=task.output_path,
            receipt_path=None,
            extra={
                "attempts_used": repair_state.get("attempts_used"),
                "made_progress": made_progress,
                "max_attempts": repair_state.get("max_attempts"),
            },
        )
        publish_image_result(repo, parent_ctx.change_id, result)
        if status == "ACCEPTED":
            receipt = publish_receipt(
                repo,
                parent_ctx.change_id,
                task,
                technical=technical,
                visual=visual,
            )
            result = result.model_copy(
                update={"receipt_path": str(receipt.relative_to(repo)).replace("\\", "/")},
            )
            publish_image_result(repo, parent_ctx.change_id, result)
        return result
