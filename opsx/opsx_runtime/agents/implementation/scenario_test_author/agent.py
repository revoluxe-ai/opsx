"""Runtime hop: canonical scenario tests сразу после apply-design, до слайсов."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.implementation.scenario_test_author.helpers import scenarios_needing_authoring
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    ensure_workflow_deferred_for_scenario,
)
from agents.implementation.scenario_test_author.prompt.get_prompt import get_prompt
from agents.implementation.scenario_test_author.schemas import ScenarioTestAuthorAnswer
from agents.repair.helpers import MUTATING
from agents.state import GraphState
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root

# Authoring hop: scoped MCP read + edit; без grep/glob/ls/shell (repo_root_scan).
_SCENARIO_TEST_AUTHOR_TOOLS = tuple(
    t for t in MUTATING if t not in ("grep", "glob", "ls", "shell")
)

_STREAM_AGENT = "scenario_test_author"


class ScenarioTestAuthorAgent(ABCAgent):
    """Пишет tests/scenarios/<change>/; не трогает slice tests и openspec."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Authoring hop с построчной идемпотентностью."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo_path = resolve_opsx_repository_root(Path(str(state["repository_path"])))
        workspace = str(normalize_workspace_path(state.get("workspace"), repo_path))
        pending = scenarios_needing_authoring(repo_path, change_id)
        if not pending:
            return {
                "scenario_authoring_status": "COMPLETE",
                "scenario_authoring_result": {"status": "ALREADY_CURRENT", "pending": []},
            }
        prompt = await get_prompt(
            repository_path=workspace,
            change_id=change_id,
            scenario_ids=pending,
        )
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
            ),
            prompt=prompt,
            agent_key="scenario_test_author",
            tools=_SCENARIO_TEST_AUTHOR_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ScenarioTestAuthorAnswer,
            format_instructions=schema_format_reminder(ScenarioTestAuthorAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ScenarioTestAuthorAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        for scenario_id in pending:
            _ = ensure_workflow_deferred_for_scenario(repo_path, change_id, scenario_id)
        still_pending = scenarios_needing_authoring(repo_path, change_id)
        ok = not still_pending
        return {
            **agent_session_patch(state, agent_id=outcome.agent_id, agent_id_key="agent_id"),
            "scenario_authoring_status": "COMPLETE" if ok else "PAUSED",
            "scenario_authoring_result": {
                "status": answer.status,
                "pending_after": still_pending,
                "authored_count": answer.authored_count,
                "block_reason": answer.block_reason,
            },
        }
