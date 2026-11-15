"""Deterministic compose через Pillow (production default)."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import hashlib
from pathlib import Path

from agents.agent_tools.image.ports.protocols import ImageGenerationRequest
from agents.agent_tools.image.revision import output_file_revision


def _sync_compose(request: ImageGenerationRequest) -> dict[str, object]:
    """Letterbox/resize к IHDR задачи; фон + простая заливка без shell."""
    from agents.agent_tools.image.png_codec import (
        _decode_png_rgba,
        _encode_png_rgba,
        _resize_rgba,
    )

    task = request.task
    out = request.output_path.resolve()
    repo = request.repo_root.resolve()
    expected = (resource_path(repo, task.output_path.replace("\\", "/"))).resolve()
    if out != expected:
        return {"ok": False, "code": "OUTPUT_PATH_MISMATCH", "output_revision": ""}
    if not str(out).startswith(str(repo)):
        return {"ok": False, "code": "OUTPUT_OUTSIDE_REPO", "output_revision": ""}

    out.parent.mkdir(parents=True, exist_ok=True)
    w, h = int(task.width), int(task.height)
    bg = (12, 18, 28, 255)
    pixels = [bg] * (w * h)

    for ref in task.style_refs:
        ref_path = (resource_path(repo, ref)).resolve()
        if ref_path.is_file():
            try:
                sw, sh, src_px = _decode_png_rgba(ref_path)
                resized = _resize_rgba(sw, sh, src_px, w, h)
                pixels = resized
                break
            except (OSError, ValueError):
                continue

    _encode_png_rgba(out, w, h, pixels)
    rev = output_file_revision(out)
    return {
        "ok": True,
        "code": "COMPOSED",
        "output_revision": rev,
        "lane": "compose",
    }


class ComposeImageGenerationPort:
    """Production порт: compose/letterbox без shell."""

    async def generate(self, request: ImageGenerationRequest) -> dict[str, object]:
        return await asyncio.to_thread(_sync_compose, request)
