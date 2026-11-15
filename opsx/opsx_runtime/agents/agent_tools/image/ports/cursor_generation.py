"""Provider generative lane: compose fallback до подтверждённого SDK API (Gate 0)."""

from __future__ import annotations

from agents.agent_tools.image.ports.compose_generation import ComposeImageGenerationPort
from agents.agent_tools.image.ports.protocols import ImageGenerationRequest
from agents.agent_tools.image.schemas import ImageGenerationMode


class CursorProviderImageGenerationPort:
    """Generative: при ``generative_required`` — compose + метка; иначе BLOCKED.

    Spike (``test_image_provider_capability_spike``) документирует, когда
    подключён реальный Cursor bridge generate. Пока нет API в репозитории —
    compose fallback для ``compose_only`` / ``agent_choice``, BLOCKED для
    ``generative_required`` если compose не разрешён.
    """

    def __init__(self, *, allow_compose_fallback: bool = True) -> None:
        self._allow_compose_fallback = allow_compose_fallback
        self._compose = ComposeImageGenerationPort()

    async def generate(self, request: ImageGenerationRequest) -> dict[str, object]:
        mode = request.task.generation_mode
        if request.use_generative or mode == ImageGenerationMode.GENERATIVE_REQUIRED:
            if not self._allow_compose_fallback:
                return {
                    "ok": False,
                    "code": "GENERATION_UNAVAILABLE",
                    "output_revision": "",
                    "message": "Cursor generative API not wired; configure alternate port",
                }
            # Gate 0: until real API, generative_required uses compose with explicit lane tag
            result = await self._compose.generate(request)
            if result.get("ok"):
                result = dict(result)
                result["lane"] = "cursor_compose_fallback"
            return result
        return await self._compose.generate(request)
