"""Runtime-агент подготовки среды и harness на первом проходе verify."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.repair.helpers import MUTATING
from agents.state import GraphState
from agents.verify.environment_prep.cohort_detect import (
    detect_environment_prep_cohorts,
    environment_prep_attempts,
)
from agents.verify.environment_prep.dispatch_item import build_environment_prep_dispatch_item
from agents.verify.environment_prep.collect_proof import run_environment_prep_collect_proof
from agents.verify.environment_prep.input_pack import build_environment_prep_input_pack
from agents.verify.environment_prep.prompt.get_prompt import get_prompt
from agents.verify.environment_prep.queue_shrink import cohort_finding_ids_for_resolution
from agents.verify.environment_prep.schemas import EnvironmentPrepResult
from workflows.repair.schemas import FixEvidence


class VerifyEnvironmentPrepAgent:
    """Пакетный harness/env repair до per-finding dispatch."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def _maybe_commit(self, state: GraphState, patch: dict[str, Any]) -> dict[str, Any]:
        if patch.get("verify_finding_fix") is None:
            return {}
        from workflows.verify.dispatch_cycle.commit_node import run_verify_finding_commit

        return await run_verify_finding_commit(state, repair_adapter=self._adapter)

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Один LLM-hop на когорты + collect proof + resolved finding ids."""
        change_id = str(state.get("change_id") or "").strip()
        repo = str(state.get("repository_path") or state.get("workspace") or ".")
        cohorts = detect_environment_prep_cohorts(state)
        attempt = environment_prep_attempts(state) + 1

        if not cohorts.cohorts:
            no_action = EnvironmentPrepResult(
                status="NO_ACTION",
                summary="No harness/env cohorts detected; skip environment_prep.",
                gates_rerun_recommended=False,
            )
            return {
                "environment_prep_result": no_action.model_dump(mode="json"),
                "environment_prep_attempts": attempt,
                "environment_prep_execution_status": "no_action",
            }

        package = build_environment_prep_input_pack(
            state=state,
            repository_path=repo,
            change_id=change_id or "unknown",
        )
        prompt = get_prompt(input_pack_json=package.model_dump_json())
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log") or "",
                stream_label=compose_agent_stream_label(state, "verify_environment_prep"),
            ),
            prompt=prompt,
            agent_key="verify_environment_prep",
            tools=MUTATING,
            resume_agent_id=None,
            payload_model=EnvironmentPrepResult,
            format_instructions=schema_format_reminder(EnvironmentPrepResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: EnvironmentPrepResult = outcome.parsed  # type: ignore[assignment]
        result_dict = parsed.model_dump(mode="json")

        collect_proof = parsed.collect_proof
        if parsed.status in {"FIXED", "PARTIAL"} and parsed.files_changed:
            proof = run_environment_prep_collect_proof(repo)
            collect_proof = proof
            result_dict["collect_proof"] = proof.model_dump(mode="json")
            if proof.collect_ok and not parsed.gates_rerun_recommended:
                result_dict["gates_rerun_recommended"] = True

        fix: dict[str, Any] | None = None
        if parsed.status in {"FIXED", "PARTIAL"} and parsed.files_changed:
            fix = FixEvidence(
                status="FIXED",
                summary=parsed.summary,
                files_changed=list(parsed.files_changed),
                notes=parsed.notes,
            ).model_dump(mode="json")

        resolved_ids: list[str] = []
        collect_ok = bool(collect_proof and collect_proof.collect_ok)
        resolved_ids = cohort_finding_ids_for_resolution(
            cohorts_addressed=list(parsed.cohorts_addressed),
            cohorts_pack=cohorts.model_dump(mode="json"),
            collect_ok=collect_ok,
        )

        patch: dict[str, Any] = {
            "environment_prep_result": result_dict,
            "environment_prep_attempts": attempt,
            "environment_prep_execution_status": outcome.status,
            "environment_prep_agent_id": outcome.agent_id,
            "environment_prep_run_id": outcome.run_id,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="environment_prep_agent_id",
            ),
        }
        if fix is not None:
            patch["verify_finding_fix"] = fix
            patch["dispatcher_current_item"] = build_environment_prep_dispatch_item(
                state,
                cohorts=cohorts,
                summary=parsed.summary,
            )
        if resolved_ids:
            prior = state.get("environment_prep_resolved_finding_ids")
            merged = list(prior) if isinstance(prior, list) else []
            for fid in resolved_ids:
                if fid not in merged:
                    merged.append(fid)
            patch["environment_prep_resolved_finding_ids"] = merged

        mini_state = {**state, **patch}
        patch.update(await self._maybe_commit(mini_state, patch))
        return patch
