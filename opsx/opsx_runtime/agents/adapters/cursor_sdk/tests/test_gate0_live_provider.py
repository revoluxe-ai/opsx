"""Gate 0 live: реальный Cursor provider + контрольная картинка."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agents.adapters.cursor_sdk.repair_cursor_sdk_adapter import RepairCursorSDKAdapter
from agents.agent_tools.image.gate0_fixture import (
    GATE0_CONTROL_REL,
    GATE0_VISUAL_SECRET,
    write_gate0_control_png,
)
from agents.agent_tools.image.gate0_provider_probe import (
    answer_reports_gate0_secret,
    run_gate0_executor_sight_probe,
    run_gate0_reviewer_sight_probe,
)
from agents.agent_tools.image.ports.compose_generation import ComposeImageGenerationPort
from agents.agent_tools.image.ports.protocols import ImageGenerationRequest
from agents.agent_tools.image.schemas import (
    ImageDesignTask,
    ImageGenerationMode,
    ImageTaskAcceptance,
)


def _has_live_cursor() -> bool:
    return bool(os.environ.get("CURSOR_API_KEY", "").strip())


@pytest.mark.gate0_live
@pytest.mark.asyncio
async def test_gate0_executor_sees_control_detail(tmp_path: Path) -> None:
    if not _has_live_cursor():
        pytest.skip("CURSOR_API_KEY required for live Gate 0")
    (tmp_path / "opsx").mkdir()
    write_gate0_control_png(tmp_path / GATE0_CONTROL_REL)
    adapter = RepairCursorSDKAdapter(timeout_seconds=600)
    adapter.agent_tool_registry = None  # type: ignore[attr-defined]
    answer = await run_gate0_executor_sight_probe(adapter, tmp_path)
    assert answer_reports_gate0_secret(answer), (
        f"executor did not report secret; got {answer.model_dump()!r}; "
        f"expected marker like {GATE0_VISUAL_SECRET!r}"
    )


@pytest.mark.gate0_live
@pytest.mark.asyncio
async def test_gate0_reviewer_sees_control_detail(tmp_path: Path) -> None:
    if not _has_live_cursor():
        pytest.skip("CURSOR_API_KEY required for live Gate 0")
    (tmp_path / "opsx").mkdir()
    write_gate0_control_png(tmp_path / GATE0_CONTROL_REL)
    out_rel = "gate0/candidate.png"
    task = ImageDesignTask(
        task_id="gate0.probe",
        purpose="compose candidate",
        output_path=out_rel,
        width=128,
        height=96,
        style_refs=[GATE0_CONTROL_REL],
        acceptance=ImageTaskAcceptance(ihdr=[128, 96], visual_rubric="generic"),
        generation_mode=ImageGenerationMode.COMPOSE_ONLY,
    )
    port = ComposeImageGenerationPort()
    await port.generate(
        ImageGenerationRequest(
            task=task,
            output_path=tmp_path / out_rel,
            repo_root=tmp_path,
        ),
    )
    adapter = RepairCursorSDKAdapter(timeout_seconds=600)
    answer = await run_gate0_reviewer_sight_probe(adapter, tmp_path, candidate_rel=out_rel)
    assert answer_reports_gate0_secret(answer), (
        f"reviewer did not report secret; got {answer.model_dump()!r}"
    )
