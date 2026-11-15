"""Real OPSX verification backend using existing Cursor SDK and ledger contracts.

The only difference from Temporal activities is who schedules their steps.
No Temporal Activity APIs or Temporal Server are used.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import sys
import uuid
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[2]
_TEMPORAL_DIR = _REPO / "tools" / "temporal"
if str(_TEMPORAL_DIR) not in sys.path:
    sys.path.insert(0, str(_TEMPORAL_DIR))

from model_config import AgentModelConfig  # noqa: E402
from sdk_cursor_adapter import make_sdk_cursor_adapter  # noqa: E402
from verification_engine import (  # noqa: E402
    build_execute_prompt,
    build_next_check_prompt,
    classify_next_check,
    inspect_expected_result,
    validate_assignment_for_execute,
)
from verification_plan_bridge import resolve_verification_plan_context  # noqa: E402


class RealVerificationBackend:
    """Runs authentic OPSX /opsx-next-check and the ledger-assigned executor."""

    def __init__(self, repo: str | Path, *, run_id: str, model_config: str | None = None):
        self.repo = Path(repo).resolve()
        self.run_id = run_id
        self.models = AgentModelConfig.load(model_config)

    def _adapter(self):
        # Each hop MUST have a fresh Cursor session; ledger is authority.
        key = f"langgraph:{self.run_id}:{uuid.uuid4().hex[:12]}"
        adapter = make_sdk_cursor_adapter(
            self.repo, workflow_key=key, model_config=self.models
        )
        adapter.bootstrap_agent()
        return adapter

    def ensure_context(self, change: str) -> dict[str, Any]:
        return resolve_verification_plan_context(self.repo, change).to_activity_payload()

    def next_check(self, change: str) -> dict[str, Any]:
        try:
            invocation = self._adapter().execute_prompt(
                build_next_check_prompt(change), step="next_check"
            )
            raw = invocation.raw_output or ""
            completed = invocation.exit_code == 0 or bool(raw.strip())
            exit_code = invocation.exit_code
        except Exception as exc:  # transport error must not be treated as PASS
            raw = f"STATUS: NEXT_CHECK_TECHNICAL_FAILURE\n{type(exc).__name__}: {exc}"
            completed = False
            exit_code = 1
        return classify_next_check(
            repo=self.repo,
            change=change,
            raw_output=raw,
            completed=completed,
            agent_exit_code=exit_code,
        ).to_dict()

    def inspect_result(self, assignment: Mapping[str, Any]) -> dict[str, Any] | None:
        return inspect_expected_result(dict(assignment), repo=self.repo)

    def execute_assigned(
        self, change: str, assignment: Mapping[str, Any]
    ) -> dict[str, Any]:
        payload = dict(assignment)
        missing = validate_assignment_for_execute(payload)
        if missing:
            return {
                "completed": False,
                "uncertain": False,
                "durable_result_found": False,
                "durable_result_status": None,
                "expected_result_path": payload.get("expected_result_path"),
                "required_check_id": payload.get("required_check_id"),
                "executor": payload.get("executor"),
                "started": False,
                "raw_output": "STATUS: VERIFICATION_CHECK_BLOCKED\nREASON: ASSIGNMENT_INCOMPLETE\n"
                + "MISSING_FIELDS: " + ",".join(missing),
            }
        # Existing bounded verification preflight, WITHOUT Temporal requirement.
        if payload.get("bounded_verify_target"):
            path = resource_path(self.repo, ".cursor", "pipeline", "bounded-verify")
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
            from bounded_verify_runtime_preflight import (
                PreflightOutcome, preflight_static_tools,
            )

            preflight = preflight_static_tools(require_temporal=False)
            if preflight.outcome != PreflightOutcome.READY:
                return {
                    "completed": False,
                    "uncertain": False,
                    "durable_result_found": False,
                    "durable_result_status": None,
                    "expected_result_path": payload.get("expected_result_path"),
                    "required_check_id": payload.get("required_check_id"),
                    "executor": payload.get("executor"),
                    "started": False,
                    "raw_output": "STATUS: VERIFICATION_CHECK_BLOCKED\n"
                    f"REASON: RUNTIME_PREFLIGHT_{preflight.outcome.value}\n"
                    f"DETAIL: {preflight.message}",
                }

        # If identity-bound evidence already exists, do not repeat side effects.
        prior = self.inspect_result(payload)
        if prior is not None:
            return self.recovered_result(payload, prior)

        raw, exit_code, started = "", None, False
        try:
            prompt = build_execute_prompt(payload, repo=self.repo)
            adapter = self._adapter()
            started = True
            invocation = adapter.execute_prompt(prompt, step="verification_executor")
            raw, exit_code = invocation.raw_output or "", invocation.exit_code
        except Exception as exc:
            raw = f"executor exception: {type(exc).__name__}: {exc}"
        durable = self.inspect_result(payload)
        completed = exit_code == 0 or durable is not None
        uncertain = durable is None and (started or exit_code is None)

        browser_release = None
        if started and durable is not None:
            from owner_browser_check_gate import (
                is_browser_check_assignment, run_browser_check_release,
            )

            if is_browser_check_assignment(payload):
                browser_release = run_browser_check_release(
                    repo=self.repo,
                    change=change,
                    assignment=payload,
                    durable_result_status=durable.get("STATUS"),
                )

        return {
            "completed": completed,
            "uncertain": uncertain,
            "durable_result_found": durable is not None,
            "durable_result_status": durable.get("STATUS") if durable else None,
            "expected_result_path": payload.get("expected_result_path"),
            "required_check_id": payload.get("required_check_id"),
            "executor": payload.get("executor"),
            "started": started,
            "exit_code": exit_code,
            "raw_output": raw,
            "owner_browser_check_release": browser_release,
        }

    @staticmethod
    def recovered_result(assignment: Mapping[str, Any], durable: Mapping[str, Any]):
        return {
            "completed": True,
            "uncertain": False,
            "durable_result_found": True,
            "durable_result_status": durable.get("STATUS"),
            "expected_result_path": assignment.get("expected_result_path"),
            "required_check_id": assignment.get("required_check_id"),
            "executor": assignment.get("executor"),
            "started": False,
            "raw_output": "RECOVERED_FROM_DURABLE_EVIDENCE",
        }
