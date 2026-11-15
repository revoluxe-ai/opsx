"""Промпт apply-design hop: встроенный stage authority + контекст change."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.capabilities.manifest import AgentCapabilityManifest
from agents.implementation.apply_design.prompt.build_input_pack import (
    build_apply_design_input_pack,
    format_input_pack_prompt_block,
)
from agents.implementation.prompt.build_split_apply_prompt import build_split_apply_prompt

_STAGE_KEY = "apply_design"
_PROMPT_DIR = Path(__file__).resolve().parent
_CAPABILITY_SURFACE = (_PROMPT_DIR / "capability_surface.txt").read_text(encoding="utf-8")


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None = None,
    base_digest: str = "",
    manifest: AgentCapabilityManifest | None = None,
) -> str:
    """Собрать prompt агента apply-design для вызова адаптера."""
    body = build_split_apply_prompt(
        stage_key=_STAGE_KEY,
        repository_path=repository_path,
        change_id=change_id,
        impl_verify_answer=impl_verify_answer,
        artifact_bundle=artifact_bundle,
        machine_context=machine_context,
    )
    input_block = ""
    if manifest is not None:
        pack = await build_apply_design_input_pack(manifest)
        input_block = format_input_pack_prompt_block(pack)
    return (
        body
        + input_block
        + _CAPABILITY_SURFACE.format(base_digest=base_digest or '""')
    )


def get_prompt_sync(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None = None,
    base_digest: str = "",
    input_pack: dict[str, Any] | None = None,
) -> str:
    """Синхронная сборка prompt (unit-тесты без event loop)."""
    body = build_split_apply_prompt(
        stage_key=_STAGE_KEY,
        repository_path=repository_path,
        change_id=change_id,
        impl_verify_answer=impl_verify_answer,
        artifact_bundle=artifact_bundle,
        machine_context=machine_context,
    )
    input_block = ""
    if input_pack is not None:
        input_block = format_input_pack_prompt_block(input_pack)
    return (
        body
        + input_block
        + _CAPABILITY_SURFACE.format(base_digest=base_digest or '""')
    )
