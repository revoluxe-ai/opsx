"""Сборка SDK-prompt для repair/hunt AI-hop (без wire-протокола repair.agent.v1)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from workflows.repair.schemas import RepairState
from model_config.agent_roles import AgentRole
from agents.common_prompt_data.repair import REPAIR_AGENT_SHARED_RULES
from agents.repair.embedded_authority import embed_repair_worker_authority


def build_role_prompt(
    state: RepairState,
    *,
    role: AgentRole,
    role_instructions: str,
    payload_model: type[BaseModel],
    context: dict[str, Any],
) -> str:
    """Prompt: worker contract + TASK + INPUT CONTEXT JSON.

    JSON-схему ответа не вставляет: её добавляет ``ABCAgent`` из ``payload_model``.
    Аргумент ``payload_model`` оставлен для вызывающих и в текст не попадает.
    """
    _ = payload_model
    wire_context = dict(context)
    if state.preflight is not None:
        wire_context["preflight"] = state.preflight.model_dump(mode="json")
    context_json = json.dumps(wire_context, ensure_ascii=False, indent=2)
    authority_block = embed_repair_worker_authority(
        str(role), repository_path=state.repository_path
    )
    return f"""You are the {role} step in an OPSX LangGraph Bug Team.

Mission: {state.mission_id}
Repository: {state.repository_path}
Workspace: {state.workspace}

AUTHORITATIVE CONTEXT (embedded — do not open `.cursor/**`):
{authority_block}
{REPAIR_AGENT_SHARED_RULES}

ROLE-SPECIFIC TASK:
{role_instructions}

INPUT CONTEXT (JSON):
{context_json}
"""


build_prompt = build_role_prompt
build_repair_agent_prompt = build_role_prompt
