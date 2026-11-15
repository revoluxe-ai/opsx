"""Resolve модели для agent hop."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .agent_roles import AgentRole
from .bug_bot_model import high_model, low_model, medium_model
from .model_presets import DEFAULT_HOP_MODEL
from .models.base_model_setting import BaseModelSetting


class ModelResolveWorkflow(StrEnum):
    """Маркер workflow, который сам задаёт круг и лестницу моделей."""

    SEARCH_ROUNDS = "search_rounds"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ModelResolveRequest(StrictModel):
    role: AgentRole
    mission_id: str = Field(min_length=1)
    hop: str | None = None
    workflow: ModelResolveWorkflow | None = None
    round: int = Field(default=1, ge=1)
    task_summary: str | None = None


class ModelResolveResult(StrictModel):
    model: BaseModelSetting


def _resolve_search_rounds_model(round: int) -> BaseModelSetting | None:
    """Лестница моделей bounded hunt: эскалация после серии CLEAR."""
    if round == 1:
        return low_model
    if round == 2:
        return medium_model
    if round == 3:
        return high_model
    return None


def resolve_model(request: ModelResolveRequest) -> ModelResolveResult:
    if request.workflow is ModelResolveWorkflow.SEARCH_ROUNDS:
        profile = _resolve_search_rounds_model(request.round)
        if profile is None:
            raise ValueError(
                f"no model preset for workflow={request.workflow!s} "
                f"round={request.round}",
            )
        return ModelResolveResult(model=profile)

    return ModelResolveResult(model=DEFAULT_HOP_MODEL)
