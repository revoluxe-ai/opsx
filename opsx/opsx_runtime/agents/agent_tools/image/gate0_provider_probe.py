"""Живой Gate 0: модель должна назвать секрет с контрольной картинки."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agents.abc_agent import ABCAgent
from agents.agent_tools.image.child_tools import (
    ImageAssetChildToolRuntime,
    build_image_child_custom_tools,
)
from agents.agent_tools.image.gate0_fixture import GATE0_CONTROL_REL, GATE0_VISUAL_SECRET
from agents.agent_tools.image.ports.cursor_generation import CursorProviderImageGenerationPort
from agents.agent_tools.image.ports.model_input import ImageModelInputPort
from agents.agent_tools.image.ports.render import ImageRenderPort
from agents.agent_tools.image.schemas import (
    ImageDesignTask,
    ImageGenerationMode,
    ImageTaskAcceptance,
)
from agents.adapters.abc_adapter import ABCAdapter, schema_format_reminder


class Gate0SightAnswer(BaseModel):
    """Ответ probe-hop: секрет только из изображения."""

    status: str = Field(description="SEEN|NOT_SEEN|BLOCKED")
    observed_marker: str = Field(
        description="Exact marker text read from image via read_image_page",
    )
    evidence_tool: str = ""


def _build_probe_task(repo: Path, *, output_path: str = "gate0/out.png") -> ImageDesignTask:
    return ImageDesignTask(
        task_id="gate0.probe",
        purpose="Gate0 image sight check",
        output_path=output_path,
        width=128,
        height=96,
        copy_text=["Generic placeholder only"],
        style_refs=[GATE0_CONTROL_REL],
        acceptance=ImageTaskAcceptance(
            ihdr=[128, 96],
            visual_rubric="Reference alignment only",
        ),
        generation_mode=ImageGenerationMode.COMPOSE_ONLY,
    )


def _probe_prompt(control_rel: str) -> str:
    return (
        "Gate 0 provider probe. Task text deliberately omits the hidden marker.\n"
        f"Call read_image_page on path {control_rel!r} (all pages until eof).\n"
        "Report the exact marker string visible in the image (not guessed).\n"
        f"If you cannot see it, status=NOT_SEEN and observed_marker empty.\n"
        "Do not guess the marker; only report after read_image_page.\n"
    )


async def run_gate0_executor_sight_probe(
    adapter: ABCAdapter,
    repo_root: Path,
    *,
    model: dict[str, Any] | None = None,
) -> Gate0SightAnswer:
    """Исполнитель: child tools + structured ответ."""
    repo = repo_root.resolve()
    task = _build_probe_task(repo)
    runtime = ImageAssetChildToolRuntime(
        repo_root=repo,
        task=task,
        generation=CursorProviderImageGenerationPort(allow_compose_fallback=True),
        model_input=ImageModelInputPort(repo_root=repo),
        render=ImageRenderPort(repo_root=repo),
    )
    transport: dict[str, Any] = {
        "repository_path": str(repo),
        "workspace": str(repo),
        "model": model or {"id": "composer-2.5"},
        "image_child_custom_tools": build_image_child_custom_tools(runtime),
    }
    outcome = await ABCAgent.execute_validated(
        adapter,
        transport,
        prompt=_probe_prompt(GATE0_CONTROL_REL),
        tools=("mcp",),
        resume_agent_id=None,
        payload_model=Gate0SightAnswer,
        format_instructions=schema_format_reminder(Gate0SightAnswer),
        agent_key="image_asset",
    )
    if outcome.parsed is None:
        return Gate0SightAnswer(status="BLOCKED", observed_marker="")
    return Gate0SightAnswer.model_validate(outcome.parsed.model_dump(mode="json"))


async def run_gate0_reviewer_sight_probe(
    adapter: ABCAdapter,
    repo_root: Path,
    *,
    candidate_rel: str,
    model: dict[str, Any] | None = None,
) -> Gate0SightAnswer:
    """Reviewer: читает output + ref, не получает секрет в промпте."""
    repo = repo_root.resolve()
    task = _build_probe_task(repo, output_path=candidate_rel)
    runtime = ImageAssetChildToolRuntime(
        repo_root=repo,
        task=task,
        generation=CursorProviderImageGenerationPort(allow_compose_fallback=True),
        model_input=ImageModelInputPort(repo_root=repo),
        render=ImageRenderPort(repo_root=repo),
    )
    allowed = {GATE0_CONTROL_REL, candidate_rel.replace("\\", "/")}
    transport: dict[str, Any] = {
        "repository_path": str(repo),
        "workspace": str(repo),
        "model": model or {"id": "composer-2.5"},
        "image_child_custom_tools": build_image_child_custom_tools(runtime),
    }
    prompt = (
        _probe_prompt(GATE0_CONTROL_REL)
        + f"\nAlso read {candidate_rel!r} if needed. Allowed paths: {sorted(allowed)}.\n"
        "You are visual reviewer role.\n"
    )
    outcome = await ABCAgent.execute_validated(
        adapter,
        transport,
        prompt=prompt,
        tools=("mcp",),
        resume_agent_id=None,
        payload_model=Gate0SightAnswer,
        format_instructions=schema_format_reminder(Gate0SightAnswer),
        agent_key="image_asset_visual_review",
    )
    if outcome.parsed is None:
        return Gate0SightAnswer(status="BLOCKED", observed_marker="")
    return Gate0SightAnswer.model_validate(outcome.parsed.model_dump(mode="json"))


def answer_reports_gate0_secret(answer: Gate0SightAnswer) -> bool:
    """PASS если модель назвала секрет (после tool), не из подсказки в user message."""
    if answer.status != "SEEN":
        return False
    token = answer.observed_marker.strip().upper()
    if not token:
        return False
    return bool(re.search(r"7F3A", token)) and "MARK" in token
