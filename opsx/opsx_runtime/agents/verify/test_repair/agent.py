"""Runtime-агент Test Repair для test workflow (TEST после triage)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_when_same_finding,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
# Test repair: scoped read + edit + MCP run_pytest; без grep/glob/ls/shell.
_TEST_REPAIR_TOOLS = (
    "read",
    "edit",
    "run_pytest",
)
from agents.state import GraphState
from agents.verify.test_repair.input_pack import build_test_repair_input_pack, pack_json
from agents.verify.test_repair.prompt.get_prompt import get_prompt
from agents.verify.test_repair.schemas import TestRepairResult
from agents.verify.test_repair.scope_denial_handoff import (
    maybe_scope_denial_handoff_from_repair,
)
from agents.verify.test_repair.triage_handoff import resolve_test_triage_for_repair
from workflows.repair.schemas import FixEvidence


class TestRepairAgent:
    """Исполнитель test-fix для одного pytest finding."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Положить ``verify_finding_fix`` для общего commit hop."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for test_repair")

        from agents.verify.group_error.schemas import GroupErrorResult
        from agents.verify.pytest_run_health import is_harness_blocker

        raw = state.get("analysis_result")
        if isinstance(raw, dict):
            by_id = {f.finding_id: f for f in GroupErrorResult.model_validate(raw).findings}
            group_ids = item.get("group_finding_ids") or [item.get("finding_id")]
            for fid in group_ids:
                if not fid:
                    continue
                finding = by_id.get(str(fid))
                if finding is not None and is_harness_blocker(finding.repair_workflow):
                    blocked = TestRepairResult(
                        finding_id=str(fid),
                        status="BLOCKED",
                        summary="WF_PYTEST_COLLECTION findings use pytest_execute harness path",
                    )
                    return {
                        "test_repair_result": blocked.model_dump(mode="json"),
                        "verify_finding_fix": None,
                        "test_repair_execution_status": "BLOCKED",
                    }

        triage_raw = resolve_test_triage_for_repair(state)

        change_id = str(state.get("change_id") or "").strip()
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        package = build_test_repair_input_pack(
            repository_path=repo,
            item=item,
            state=state,
            triage=triage_raw,
        )
        prompt = get_prompt(input_pack_json=pack_json(package))
        finding_id = str(item.get("finding_id") or "")
        # Finding IDs are positional (F-0001 can name a *different test* in
        # the next full gate). Resume only if the same gate/cohort evidence
        # and observation remain current.
        fingerprint_payload = {
            "finding_id": finding_id,
            "observation": str(item.get("observation") or ""),
            "group_id": str(item.get("group_id") or ""),
            "reproduction_cohort": item.get("reproduction_cohort"),
            "quality_gate_raw": state.get("quality_gate_raw"),
            "change_id": change_id,
        }
        input_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload, sort_keys=True, ensure_ascii=False, default=str,
            ).encode("utf-8")
        ).hexdigest()
        prior = state.get("test_repair_result")
        same_input = (
            isinstance(prior, dict)
            and prior.get("input_fingerprint") == input_fingerprint
        )

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("test_repair_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "test_repair"),
            ),
            prompt=prompt,
            agent_key="test_repair",
            tools=_TEST_REPAIR_TOOLS,
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=(
                    state.get("test_repair_agent_id") if same_input else None
                ),
                bound_run_uuid=state.get("test_repair_session_run_uuid"),
                prior_result=state.get("test_repair_result"),
                finding_id=finding_id,
                unit_id=str(item.get("unit_id") or item.get("repair_unit_id") or ""),
            ),
            payload_model=TestRepairResult,
            format_instructions=schema_format_reminder(TestRepairResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TestRepairResult = outcome.parsed  # type: ignore[assignment]
        parsed = parsed.model_copy(
            update={
                "finding_id": str(item.get("finding_id") or parsed.finding_id),
                "input_fingerprint": input_fingerprint,
            },
        )
        from agents.verify.repair.hop_cannot_complete import raise_if_repair_hop_cannot_complete

        raise_if_repair_hop_cannot_complete(
            hop="test_repair",
            finding_id=parsed.finding_id,
            status=parsed.status,
            summary=parsed.summary,
        )
        result_dict = parsed.model_dump(mode="json")
        fix: dict[str, Any] | None = None
        if parsed.status == "FIXED":
            fix = FixEvidence(
                status="FIXED",
                summary=parsed.summary,
                files_changed=list(parsed.files_changed),
                notes=parsed.notes,
            ).model_dump(mode="json")

        repo_root: Path | None = None
        try:
            repo_root = Path(repo).resolve()
        except (OSError, TypeError, ValueError):
            repo_root = None
        scope_patch = maybe_scope_denial_handoff_from_repair(
            state,
            status=parsed.status,
            summary=parsed.summary,
            notes=parsed.notes,
            recorded_violation=getattr(
                outcome,
                "recorded_outside_restrict_allow",
                None,
            ),
            repo_root=repo_root,
        )
        repair_outcome: dict[str, Any] = {
            "workflow": "test_repair",
            "result": result_dict,
        }
        if scope_patch.get("verify_finding_repair_outcome"):
            repair_outcome = dict(scope_patch["verify_finding_repair_outcome"])
            repair_outcome["result"] = result_dict

        return {
            "test_repair_result": result_dict,
            "verify_finding_fix": fix,
            "verify_finding_repair_outcome": repair_outcome,
            **scope_patch,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="test_repair_agent_id",
            ),
            "test_repair_run_id": outcome.run_id,
            "test_repair_execution_status": outcome.status,
            "test_repair_usage": outcome.usage,
            "test_repair_cost": outcome.cost,
            "test_repair_cost_status": outcome.cost_status,
            "test_repair_billing_error": outcome.billing_error,
            "test_repair_usage_source": outcome.usage_source,
            "test_repair_validation_attempts": outcome.validation_attempts,
        }
