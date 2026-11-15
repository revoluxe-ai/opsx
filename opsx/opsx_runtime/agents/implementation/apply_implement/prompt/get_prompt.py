"""Промпт apply-implement hop: встроенный stage authority + контекст change."""

from __future__ import annotations

from typing import Any

from agents.implementation.prompt.build_split_apply_prompt import build_split_apply_prompt

_STAGE_KEY = "apply_implement"


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any] | None = None,
) -> str:
    """Собрать SDK prompt для ``ApplyImplementAgent``."""
    return build_split_apply_prompt(
        stage_key=_STAGE_KEY,
        repository_path=repository_path,
        change_id=change_id,
        impl_verify_answer=impl_verify_answer,
        artifact_bundle=artifact_bundle,
        machine_context=machine_context,
    )
