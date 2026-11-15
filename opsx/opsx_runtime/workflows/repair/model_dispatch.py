"""Минимальный мост: state + role → ModelResolveRequest → resolve_model."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import sys
from pathlib import Path

from workflows.repair.schemas import RepairState

REPO_ROOT = project_root()
_CONFIGS_ROOT = resource_path(REPO_ROOT, "opsx", "configs")
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))

from model_config.agent_roles import AgentRole, coerce_agent_role  # noqa: E402
from model_config.resolve_model import (  # noqa: E402
    ModelResolveRequest,
    ModelResolveResult,
    ModelResolveWorkflow,
    resolve_model,
)


SEARCH_MODEL_MAX_ROUND = 3


def search_model_round(state: RepairState) -> int:
    """Номер круга для следующего bounded hunt.

    Круг = один проход поиска. Подряд идущие ``CLEAR`` в ``state.hunts`` означают,
    что предыдущие модели «не нашли» — следующий hunt получает более высокий round
    (1 → low, 2 → medium, 3 → high). Любой другой ``hunt_result`` обнуляет серию.
    """
    clear_streak = 0
    for hunt in reversed(state.hunts):
        if hunt.hunt_result == "CLEAR":
            clear_streak += 1
        else:
            break
    return min(clear_streak + 1, SEARCH_MODEL_MAX_ROUND)


def resolve_model_for_role(
    state: RepairState,
    *,
    role: AgentRole,
    hop: str,
    task_summary: str | None = None,
) -> ModelResolveResult:
    """Единственная точка resolve для ``execute_role``."""
    agent_role = coerce_agent_role(role)
    workflow: ModelResolveWorkflow | None = None
    round_num = 1
    if agent_role is AgentRole.BOUNDED_HUNTER:
        workflow = ModelResolveWorkflow.SEARCH_ROUNDS
        round_num = search_model_round(state)

    request = ModelResolveRequest(
        role=agent_role,
        mission_id=state.mission_id,
        hop=hop,
        workflow=workflow,
        round=round_num,
        task_summary=task_summary or str(agent_role),
    )
    return resolve_model(request)
