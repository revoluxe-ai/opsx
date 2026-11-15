"""Stub generation — только тесты."""

from __future__ import annotations

from agents.agent_tools.image.ports.protocols import ImageGenerationRequest


class StubImageGenerationPort:
    """Всегда пишет через compose-поведение или возвращает BLOCKED."""

    def __init__(self, *, delegate_ok: bool = True) -> None:
        self._delegate_ok = delegate_ok

    async def generate(self, request: ImageGenerationRequest) -> dict[str, object]:
        if not self._delegate_ok:
            return {"ok": False, "code": "STUB_BLOCKED", "output_revision": ""}
        from agents.agent_tools.image.ports.compose_generation import (
            ComposeImageGenerationPort,
        )

        compose = ComposeImageGenerationPort()
        return await compose.generate(request)
