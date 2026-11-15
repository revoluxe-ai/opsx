"""Загрузка agent_budget_profiles.json и map role → profile."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class AgentBudgetProfile(BaseModel):
    """Лимиты одного hop / task для роли агента."""

    model_config = ConfigDict(extra="forbid")

    max_observed_input_per_hop: int
    max_observed_output_per_hop: int
    max_observed_input_per_task: int
    max_observed_output_per_task: int
    max_model_turns_per_hop: int = 4
    max_internal_model_events_per_hop: int = 80
    max_tool_invocations_per_hop: int = 80
    estimate_reserve_ratio: float = 1.15
    deadline_wall_clock_s: int | None = None


def _profiles_path() -> Path:
    here = Path(__file__).resolve()
    # opsx/opsx_runtime/agents/budget → repo/opsx/configs
    return here.parents[3] / "configs" / "agent_budget_profiles.json"


@lru_cache(maxsize=1)
def _load_catalog() -> tuple[dict[str, AgentBudgetProfile], dict[str, str]]:
    path = _profiles_path()
    if not path.is_file():
        default = AgentBudgetProfile(
            max_observed_input_per_hop=3_000_000,
            max_observed_output_per_hop=128_000,
            max_observed_input_per_task=25_000_000,
            max_observed_output_per_task=1_000_000,
        )
        return {"standard_impl": default}, {"default": "standard_impl"}
    data = json.loads(path.read_text(encoding="utf-8"))
    profiles_raw = data.get("profiles") or {}
    profiles = {
        key: AgentBudgetProfile.model_validate(value)
        for key, value in profiles_raw.items()
        if isinstance(value, dict)
    }
    role_defaults = data.get("role_defaults") or {}
    return profiles, {str(k): str(v) for k, v in role_defaults.items()}


def profile_id_for_role(role: str | None) -> str:
    """Ключ профиля в JSON для роли."""
    profiles, role_defaults = _load_catalog()
    token = str(role or "default").strip().upper()
    profile_id = role_defaults.get(token) or role_defaults.get("default") or "standard_impl"
    if profile_id in profiles:
        return profile_id
    return "standard_impl" if "standard_impl" in profiles else next(iter(profiles))


def resolve_profile_for_role(role: str | None) -> AgentBudgetProfile:
    """Profile для AgentRole value или agent_key."""
    profiles, role_defaults = _load_catalog()
    token = str(role or "default").strip().upper()
    profile_id = role_defaults.get(token) or role_defaults.get("default") or "standard_impl"
    return profiles.get(profile_id) or profiles.get("standard_impl") or next(iter(profiles.values()))
