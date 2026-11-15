"""Compatibility re-export."""

from agents.common_prompt_data.repair import (
    BUG_TEAM_POLICY_PATH,
    OPSX_ORCHESTRATION_PATH,
    ORCHESTRATION_PATH,
    POLICY_PATH,
    REPAIR_AGENT_SHARED_RULES,
)
from agents.repair.prompt_wire import (
    build_prompt,
    build_repair_agent_prompt,
    build_repair_wire_prompt,
    build_role_prompt,
)
from agents.repair.role_contracts import ROLE_CONTRACTS, ROLE_WORKER_CONTRACTS, worker_contract_path

__all__ = [
    "BUG_TEAM_POLICY_PATH",
    "OPSX_ORCHESTRATION_PATH",
    "ORCHESTRATION_PATH",
    "POLICY_PATH",
    "REPAIR_AGENT_SHARED_RULES",
    "ROLE_CONTRACTS",
    "ROLE_WORKER_CONTRACTS",
    "build_prompt",
    "build_repair_agent_prompt",
    "build_repair_wire_prompt",
    "build_role_prompt",
    "worker_contract_path",
]
