"""Протоколы генерации и рендера изображений."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agents.agent_tools.image.schemas import ImageDesignTask


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    """Запрос на запись PNG в allowlisted ``output_path``."""

    task: ImageDesignTask
    output_path: Path
    repo_root: Path
    prompt_hint: str = ""
    use_generative: bool = False


class ImageGenerationPort(Protocol):
    """Production/stub адаптер записи PNG."""

    async def generate(self, request: ImageGenerationRequest) -> dict[str, object]:
        """Записать PNG; вернуть метаданные (``ok``, ``code``, ``output_revision``)."""
