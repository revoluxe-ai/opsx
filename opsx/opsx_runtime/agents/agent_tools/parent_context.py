"""Контекст parent hop для вложенного agent tool (не GraphState, не registry)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentToolParentContext:
    """Immutable привязка parent → child на один вызов ``execute_image_task``."""

    change_id: str
    slice_id: str | None
    parent_agent_key: str
    repository_path: str
    parent_run_id: str | None = None
    parent_agent_id: str | None = None
