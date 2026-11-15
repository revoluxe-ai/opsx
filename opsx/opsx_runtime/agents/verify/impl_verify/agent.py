"""Runtime-агент ``/opsx-verify mode=impl`` (PHASE_IMPL)."""

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
from agents.verify.check_reuse import (
    read_clean_review, review_receipt_key, reused_review_transport_patch,
    save_clean_review,
)
from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    collect_deterministic_preflight,
    load_change_artifact_bundle,
    preflight_implies_gaps,
)
from agents.verify.impl_verify.prompt.get_prompt import get_prompt
from agents.verify.impl_verify.report import format_verify_impl_report
from agents.verify.impl_verify.schemas import VerifyImplAnswer

_LOGGER = logging.getLogger(__name__)

# PHASE_IMPL: пути в bundle/prompt — grep/glob/ls ведут к repo_root_scan и parent openspec/.
_IMPL_VERIFY_TOOLS = ("read",)


class ImplVerifyAgent(ABCAgent):
    """Read-only verify: реализация change vs planning-артефакты (без test coverage audit)."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить PHASE_IMPL hop и вернуть receipt + formatted report."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        preflight = collect_deterministic_preflight(change_id)
        artifacts = load_change_artifact_bundle(change_id)
        _LOGGER.info("Building prompt...")
        prompt = get_prompt(
            repository_path=workspace,
            change_id=change_id,
            artifact_bundle=artifacts,
        )
        key = review_receipt_key(state, role="impl_verify", prompt=prompt)
        # Preflight is always evaluated: its fresh machine diagnostics are not
        # inherited from a historical model response.
        # Only the semantic model assessment is reusable. Deterministic
        # preflight is recomputed on every run, including stale-green refresh.
        # PARTIAL preflight is NOT represented as a complete check PASS.
        preflight_ok = (
            preflight.get("config_contract", {}).get("status") == "PASS"
            and preflight.get("apply_completion_status") == "IMPL_OK"
            and not preflight_implies_gaps(preflight)
        )
        full_preflight_clean = (
            preflight.get("stale_green_refresh", {}).get("status")
            in ("PASS", "OK", "SKIP", "NO_STALE", "NOT_APPLICABLE")
        )
        cached = read_clean_review(
            key, model=VerifyImplAnswer, pass_verdict="IMPL_OK",
            clean=lambda item: item.verdict == "IMPL_OK" and not item.gap_details,
        ) if preflight_ok else None
        if cached is not None:
            answer, provenance = cached
            marker = "✅ IMPL_OK"
            return {
                "change_id": change_id,
                "mode": "impl",
                "verdict": answer.verdict,
                "marker": marker,
                "report_text": format_verify_impl_report(
                    change_id=change_id, answer=answer,
                    preflight=preflight, marker=marker,
                ),
                "answer": answer.model_dump(mode="json"),
                "deterministic_preflight": preflight,
                **agent_session_patch(state, agent_id="", agent_id_key="agent_id"),
                **reused_review_transport_patch(),
                "execution_status": (
                    "REUSED_PASS" if full_preflight_clean
                    else "REUSED_SEMANTIC_ONLY"
                ),
                "check_receipt": {
                    **provenance,
                    "coverage": "semantic_review_only",
                    "preflight_status": preflight.get("stale_green_refresh", {}).get("status"),
                },
            }

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "impl_verify"),
            ),
            prompt=prompt,
            agent_key="impl_verify",
            tools=_IMPL_VERIFY_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=VerifyImplAnswer,
            format_instructions=schema_format_reminder(VerifyImplAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = VerifyImplAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome

        machine_gaps = preflight_implies_gaps(preflight)
        if answer.verdict == "IMPL_OK" and machine_gaps:
            merged_ok: list[str] = []
            seen_ok: set[str] = set()
            for line in list(machine_gaps) + list(answer.gap_details):
                token = str(line).strip()
                if not token or token in seen_ok:
                    continue
                seen_ok.add(token)
                merged_ok.append(token)
            answer = answer.model_copy(
                update={"verdict": "IMPL_GAPS", "gap_details": merged_ok},
            )
        if answer.verdict == "IMPL_GAPS" and machine_gaps:
            merged: list[str] = []
            seen: set[str] = set()
            for line in list(machine_gaps) + list(answer.gap_details):
                token = str(line).strip()
                if not token or token in seen:
                    continue
                seen.add(token)
                merged.append(token)
            answer = answer.model_copy(update={"gap_details": merged})

        marker = "✅ IMPL_OK" if answer.verdict == "IMPL_OK" else "❌ IMPL_GAPS"
        after_key = review_receipt_key(state, role="impl_verify", prompt=prompt)
        saved = save_clean_review(
            key if key == after_key else None,
            answer=answer, pass_verdict="IMPL_OK",
            clean=preflight_ok and answer.verdict == "IMPL_OK" and not answer.gap_details,
            outcome=outcome,
        )
        report_text = format_verify_impl_report(
            change_id=change_id,
            answer=answer,
            preflight=preflight,
            marker=marker,
        )
        return {
            "change_id": change_id,
            "mode": "impl",
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
                "source": "fresh_audit", "saved": saved,
                "coverage": "semantic_review_only",
                "preflight_status": preflight.get("stale_green_refresh", {}).get("status"),
                "current_execution_id": str(state.get("run_uuid") or ""),
            },
        }
