"""Runtime-агент ``/opsx-verify mode=archive`` (PHASE_ARCHIVE)."""

from __future__ import annotations

import logging
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
from agents.verify.archive_verify.preflight import (
    collect_deterministic_preflight,
    preflight_implies_archive_gaps,
)
from agents.verify.archive_verify.prompt.get_prompt import get_prompt
from agents.verify.archive_verify.report import format_verify_archive_report
from agents.verify.archive_verify.schemas import VerifyArchiveAnswer
from agents.verify.check_reuse import (
    read_clean_review,
    review_receipt_key,
    reused_review_transport_patch,
    save_clean_review,
)
from agents.verify.impl_verify.preflight import load_change_artifact_bundle

_LOGGER = logging.getLogger(__name__)


class ArchiveVerifyAgent(ABCAgent):
    """Read-only archive verify: completeness/correctness/coherence + PA/blockers."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить PHASE_ARCHIVE hop и вернуть receipt + formatted report."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        preflight = collect_deterministic_preflight(change_id)
        artifacts = load_change_artifact_bundle(change_id)
        machine_gaps = preflight_implies_archive_gaps(preflight)
        _LOGGER.info("Building archive verify prompt...")
        prompt = get_prompt(
            repository_path=workspace,
            change_id=change_id,
            artifact_bundle=artifacts,
            preflight=preflight,
        )
        key = review_receipt_key(state, role="archive_verify", prompt=prompt)

        contract_ok = (preflight.get("config_contract") or {}).get("status") == "PASS"
        tasks = preflight.get("tasks_progress") or {}
        tasks_ok = int(tasks.get("remaining") or 0) == 0
        pa_status = str((preflight.get("product_acceptance") or {}).get("status") or "")
        pa_ok = pa_status not in ("FAIL", "BLOCKED")
        preflight_ok = (
            contract_ok
            and preflight.get("change_exists")
            and tasks_ok
            and pa_ok
            and not machine_gaps
        )

        cached = (
            read_clean_review(
                key,
                model=VerifyArchiveAnswer,
                pass_verdict="GO",
                clean=lambda item: item.verdict == "GO" and not item.gap_details,
            )
            if preflight_ok
            else None
        )
        if cached is not None:
            answer, provenance = cached
            marker = "✅ GO"
            return {
                "change_id": change_id,
                "mode": "archive",
                "verdict": answer.verdict,
                "marker": marker,
                "report_text": format_verify_archive_report(
                    change_id=change_id,
                    answer=answer,
                    preflight=preflight,
                    marker=marker,
                ),
                "answer": answer.model_dump(mode="json"),
                "deterministic_preflight": preflight,
                **agent_session_patch(state, agent_id="", agent_id_key="agent_id"),
                **reused_review_transport_patch(),
                "execution_status": "REUSED_PASS",
                "check_receipt": {
                    **provenance,
                    "coverage": "semantic_review_only",
                    "machine_gaps": machine_gaps,
                },
            }

        tools = ("read", "grep", "glob", "ls")
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "archive_verify"),
            ),
            prompt=prompt,
            agent_key="archive_verify",
            tools=tools,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=VerifyArchiveAnswer,
            format_instructions=schema_format_reminder(VerifyArchiveAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = VerifyArchiveAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome

        if machine_gaps:
            merged: list[str] = []
            seen: set[str] = set()
            for line in list(machine_gaps) + list(answer.gap_details):
                token = str(line).strip()
                if not token or token in seen:
                    continue
                seen.add(token)
                merged.append(token)
            answer = answer.model_copy(update={"gap_details": merged})
            if merged:
                answer = answer.model_copy(update={"verdict": "NO_GO"})

        if answer.gap_details and answer.verdict == "GO":
            answer = answer.model_copy(update={"verdict": "NO_GO"})

        marker = "✅ GO" if answer.verdict == "GO" else "❌ NO_GO"
        after_key = review_receipt_key(state, role="archive_verify", prompt=prompt)
        saved = save_clean_review(
            key if key == after_key else None,
            answer=answer,
            pass_verdict="GO",
            clean=preflight_ok and answer.verdict == "GO" and not answer.gap_details,
            outcome=outcome,
        )
        report_text = format_verify_archive_report(
            change_id=change_id,
            answer=answer,
            preflight=preflight,
            marker=marker,
        )
        return {
            "change_id": change_id,
            "mode": "archive",
            "verdict": answer.verdict,
            "marker": marker,
            "report_text": report_text,
            "answer": answer.model_dump(mode="json"),
            "deterministic_preflight": preflight,
            **agent_session_patch(state, agent_id=result.agent_id, agent_id_key="agent_id"),
            "run_id": result.run_id,
            "execution_status": result.status,
            "usage": result.usage,
            "cost": result.cost,
            "cost_status": result.cost_status,
            "billing_error": result.billing_error,
            "usage_source": result.usage_source,
            "validation_attempts": outcome.validation_attempts,
            "check_receipt": {
                "source": "fresh_audit",
                "saved": saved,
                "coverage": "semantic_review_only",
                "machine_gaps": machine_gaps,
                "current_execution_id": str(state.get("run_uuid") or ""),
            },
        }
