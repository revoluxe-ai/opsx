"""Runtime-агент ok_probe: проверка SDK hop с JSON ``{"answer": "OK"}``."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.state import GraphState
from agents.verify.ok_probe.prompt.get_prompt import get_prompt
from agents.verify.ok_probe.schemas import OkProbeAnswer


class OkProbeAgent(ABCAgent):
    """Probe без инструментов: модель должна вернуть валидный ``OkProbeAnswer``."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить один SDK hop и вернуть validated answer + метаданные run."""
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        prompt = get_prompt()
        tools: tuple[str, ...] = ()
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "ok_probe"),
            ),
            prompt=prompt,
            agent_key="ok_probe",
            tools=tools,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=OkProbeAnswer,
            format_instructions=schema_format_reminder(OkProbeAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = OkProbeAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome
        return {
            "answer": answer.model_dump(mode="json"),
            **agent_session_patch(state, agent_id=result.agent_id, agent_id_key="agent_id"),
            "run_id": result.run_id,
            "execution_status": result.status,
            "usage": result.usage,
            "cost": result.cost,
            "cost_status": result.cost_status,
            "billing_error": result.billing_error,
            "usage_source": result.usage_source,
            "validation_attempts": outcome.validation_attempts,
        }
