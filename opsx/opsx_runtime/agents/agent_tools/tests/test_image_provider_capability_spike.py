"""Gate 0: доставка PNG + compose generation (offline spike)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.agent_tools.image.ports.compose_generation import ComposeImageGenerationPort
from agents.agent_tools.image.ports.model_input import ImageModelInputPort
from agents.agent_tools.image.ports.protocols import ImageGenerationRequest
from agents.agent_tools.image.schemas import ImageDesignTask, ImageGenerationMode, ImageTaskAcceptance


@pytest.mark.unit
def test_image_model_input_port_pages(tmp_path: Path) -> None:
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 400)
    port = ImageModelInputPort(repo_root=tmp_path, page_size=256)
    chunk = port.page_image("x.png", offset=0)
    assert chunk.total_bytes == png.stat().st_size
    assert not chunk.eof
    chunk2 = port.page_image("x.png", offset=256)
    assert chunk2.eof


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compose_generation_writes_png(tmp_path: Path) -> None:
    task = ImageDesignTask(
        task_id="t",
        purpose="p",
        output_path="out.png",
        width=64,
        height=80,
        copy_text=["A"],
        acceptance=ImageTaskAcceptance(ihdr=[64, 80], visual_rubric="ok"),
        generation_mode=ImageGenerationMode.COMPOSE_ONLY,
    )
    port = ComposeImageGenerationPort()
    out = tmp_path / "out.png"
    req = ImageGenerationRequest(
        task=task,
        output_path=out,
        repo_root=tmp_path,
    )
    result = await port.generate(req)
    assert result.get("ok") is True
    assert out.is_file()
    from stroy.channels.telegram.invest_yalta.cover_asset_dimensions import read_png_size

    assert read_png_size(out) == (64, 80)
