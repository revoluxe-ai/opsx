"""ScenarioTestRepair prompt: те же inputs, что verify apply, test-only authority."""
from __future__ import annotations

import json
from typing import Any

from agents.agent_visible_text import (
    sanitize_mapping_for_agent_prompt,
    sanitize_value_for_agent_view,
)
from agents.implementation.prompt.opsx_prompt_fragments import (
    scenario_test_repair_constraints,
    verify_apply_hop_authority,
)


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    impl_verify_answer: dict[str, Any],
    scenario_coverage_answer: dict[str, Any],
    gaps_for_prompt: dict[str, Any],
    artifact_bundle: dict[str, Any],
    machine_context: dict[str, Any],
    repair_binding: dict[str, Any] | None = None,
) -> str:
    """Шесть verify apply sources + test-only constraints из OPSX contract_prompts."""

    def j(value: Any) -> str:
        if isinstance(value, dict):
            payload: Any = sanitize_mapping_for_agent_prompt(value)
        else:
            payload = sanitize_value_for_agent_view(value)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    constraints = scenario_test_repair_constraints()
    apply_context = verify_apply_hop_authority()

    return f"""You are ScenarioTestRepairAgent, a narrow TEST-ONLY repair executor.

Repository (workspace): {repository_path}
Change id: {change_id}
Verify gap source: scenario_coverage_verify

The fields below are the EXACT current inputs of the existing Apply hop:
ACTIVE GAPS (primary input):
{j(gaps_for_prompt)}

IMPL_VERIFY ANSWER:
{j(impl_verify_answer)}

SCENARIO COVERAGE ANSWER:
{j(scenario_coverage_answer)}

Planning artifacts bundle:
{j(artifact_bundle)}

Apply machine context:
{j(machine_context)}

FROZEN REPAIR BINDINGS (machine; edit ONLY allowed_repair_paths):
{j(repair_binding or {})}

--- BEGIN TEST-ONLY REPAIR CONSTRAINTS ---
{constraints}
--- END TEST-ONLY REPAIR CONSTRAINTS ---

--- BEGIN VERIFY APPLY CONTEXT (REFERENCE; TEST-ONLY CONSTRAINTS ABOVE WIN) ---
{apply_context}
--- END VERIFY APPLY CONTEXT ---
"""