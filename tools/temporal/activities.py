"""Temporal activities for Cursor prompt execution and parsing.

Bugbot activities keep historical names. Security and project-bug-hunt share the
same adapter cache / session restore path with different LaneSpecs.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from temporalio import activity
from temporalio.exceptions import ApplicationError

from config import REVIEW_BASE_REF
from cursor_adapter import MockCursorAdapter, build_scenario
from sdk_cursor_adapter import make_sdk_cursor_adapter
from lanes import (
    PROJECT_BUG_HUNT_LANE_ID,
    SECURITY_LANE_ID,
    get_lane,
    make_project_bug_hunt_lane,
    make_security_lane,
)
from loop_engine import BugbotRepairLoop, ReviewRepairLoop
from model_config import AgentModelConfig
from models import RepairRunResult, ReviewRunResult
from parsers import parse_bugbot_result, parse_opsx_bug_fix_result
from session_lifecycle_contract import (
    PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS,
    SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS,
)

_HEARTBEAT_INTERVAL_SECONDS = SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS


async def _run_blocking_with_heartbeat(
    fn,
    *,
    details: Any = None,
    cancel_hook: Callable[[], None] | None = None,
    on_heartbeat: Callable[[Any], None] | None = None,
):
    """Run blocking work off-thread while heartbeating (bootstrap + long hops)."""

    import contextvars

    activity.heartbeat(details)
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    fut = loop.run_in_executor(None, ctx.run, fn)
    interval = float(_HEARTBEAT_INTERVAL_SECONDS)
    try:
        while True:
            done, _pending = await asyncio.wait({fut}, timeout=interval)
            if fut in done:
                return fut.result()
            activity.heartbeat(details)
            if on_heartbeat is not None:
                on_heartbeat(details)
    except asyncio.CancelledError:
        if cancel_hook is not None:
            cancel_hook()
        raise


@dataclass
class ActivityConfig:
    mode: str
    repo_path: str
    mock_scenario: str | None = None
    model_config_path: str | None = None
    lane: str = "bugbot"
    base: str | None = None
    head: str | None = None
    change: str | None = None


@dataclass
class WorkflowRunConfig:
    mode: str
    repo_path: str
    mock_scenario: str | None = None
    lane: str = "bugbot"
    base: str | None = None
    head: str | None = None
    change: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ACTIVITY_CONFIG: ActivityConfig | None = None
_WORKFLOW_ADAPTERS: dict[str, Any] = {}
_MODEL_CONFIG: AgentModelConfig | None = None


def configure_activities(config: ActivityConfig) -> None:
    global _ACTIVITY_CONFIG, _MODEL_CONFIG
    _ACTIVITY_CONFIG = config
    _WORKFLOW_ADAPTERS.clear()
    _MODEL_CONFIG = (
        AgentModelConfig.load(config.model_config_path)
        if config.model_config_path
        else AgentModelConfig.load()
    )


def _require_config() -> ActivityConfig:
    if _ACTIVITY_CONFIG is None:
        raise RuntimeError("Activities are not configured")
    return _ACTIVITY_CONFIG


def _workflow_key() -> str:
    return activity.info().workflow_id


def _resolve_lane(payload: dict[str, Any]) -> Any:
    cfg = _require_config()
    lane_id = (payload.get("lane") or cfg.lane or "bugbot").strip().lower()
    if lane_id == PROJECT_BUG_HUNT_LANE_ID:
        return make_project_bug_hunt_lane(
            base=payload.get("base") or cfg.base or None,
            head=payload.get("head") or cfg.head or None,
            change=payload.get("change") or cfg.change or None,
        )
    if lane_id == SECURITY_LANE_ID:
        return make_security_lane(
            base=payload.get("base") or cfg.base or REVIEW_BASE_REF,
        )
    return get_lane(lane_id)


def _make_real_adapter(*, session_id: str | None = None) -> Any:
    cfg = _require_config()
    adapter = make_sdk_cursor_adapter(
        cfg.repo_path,
        workflow_key=_workflow_key(),
        session_id=session_id,
        model_config=_MODEL_CONFIG,
    )
    return adapter


def _process_client_for_cancel() -> Any:
    """Process-boundary client for bootstrap cancel / orphan cleanup."""

    root = Path(__file__).resolve().parents[2]
    executor_root = resource_path(root, "tools", "cursor_executor")
    if str(executor_root) not in sys.path:
        sys.path.insert(0, str(executor_root))
    from cursor_executor.process_client import CursorExecutorProcessClient
    from cursor_execution_activity import _default_ledger_root

    return CursorExecutorProcessClient(ledger_root=_default_ledger_root())


def _append_bootstrap_trace(execution_id: str, event: dict[str, Any]) -> None:
    """Best-effort JSONL trace adjacent to executor ledger records."""

    try:
        from cursor_execution_activity import _default_ledger_root

        record_dir = _default_ledger_root() / execution_id
        record_dir.mkdir(parents=True, exist_ok=True)
        path = record_dir / "bootstrap-trace.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _bootstrap_session_adapter(ctx: dict[str, Any]) -> Any:
    adapter = _make_real_adapter()
    from cursor_executor.execution_identity import bootstrap_execution_id

    session_epoch = str(getattr(adapter, "_session_epoch", ""))
    ctx["execution_id"] = bootstrap_execution_id(
        workflow_key=_workflow_key(),
        session_epoch=session_epoch,
    )
    adapter.bootstrap_agent()
    timings = adapter.last_bootstrap_timings_ms()
    if timings:
        ctx["bootstrap_timings_ms"] = timings
    return adapter


def _get_or_restore_adapter(config: dict[str, Any] | None = None) -> Any:
    """Return in-process adapter, or rebuild it after worker restart from session_id."""
    workflow_id = _workflow_key()
    adapter = _WORKFLOW_ADAPTERS.get(workflow_id)
    if adapter is not None:
        return adapter

    cfg = _require_config()
    payload = config or {}
    session_id = payload.get("session_id")

    if cfg.mode == "mock":
        scenario_name = payload.get("mock_scenario") or cfg.mock_scenario or "scenario1"
        adapter = MockCursorAdapter(build_scenario(scenario_name))
    elif cfg.mode == "real":
        if not session_id:
            raise RuntimeError(
                f"No Cursor adapter for workflow {workflow_id} and no session_id to restore"
            )
        adapter = _make_real_adapter(session_id=session_id)
    else:
        raise RuntimeError(f"Unsupported activity mode: {cfg.mode}")

    _WORKFLOW_ADAPTERS[workflow_id] = adapter
    activity.logger.info(
        "restored cursor adapter workflow_id=%s session_id=%s mode=%s",
        workflow_id,
        adapter.session_id(),
        cfg.mode,
    )
    return adapter


@activity.defn(name="ensure_cursor_session")
async def ensure_cursor_session(config: dict[str, Any]) -> dict[str, Any]:
    cfg = _require_config()
    workflow_id = _workflow_key()

    if cfg.mode == "mock":
        scenario_name = config.get("mock_scenario") or cfg.mock_scenario or "scenario1"
        adapter = MockCursorAdapter(build_scenario(scenario_name))
    elif cfg.mode == "real":
        client = _process_client_for_cancel()
        client.cancel_bootstrap_executions(correlation_id=workflow_id, suffix="agent-bootstrap")
        ctx: dict[str, Any] = {"execution_id": None}

        def _cancel_hook() -> None:
            eid = ctx.get("execution_id")
            if eid:
                cleaned = client.cancel_execution(str(eid), reason="activity_cancelled")
                if not cleaned:
                    activity.logger.error(
                        "bootstrap subprocess still running after cleanup budget execution_id=%s",
                        eid,
                    )

        hb_base = {
            "phase": "bootstrap",
            "workflow_id": workflow_id,
            "step": "ensure_cursor_session",
            "execution_id": None,
        }

        def _on_hb(payload: Any) -> None:
            eid = ctx.get("execution_id") or (payload.get("execution_id") if isinstance(payload, dict) else None)
            if eid:
                _append_bootstrap_trace(str(eid), {"kind": "heartbeat", "payload": payload})

        try:
            adapter = await _run_blocking_with_heartbeat(
                lambda: _bootstrap_session_adapter(ctx),
                details=hb_base,
                cancel_hook=_cancel_hook,
                on_heartbeat=_on_hb,
            )
        except Exception as exc:
            eid = ctx.get("execution_id")
            if eid:
                client.cancel_execution(str(eid), reason="bootstrap_failed")
            raise ApplicationError(
                f"session bootstrap failed: {exc}",
                type="BootstrapFailed",
                non_retryable=False,
            ) from exc
        eid = ctx.get("execution_id")
        if eid:
            hb_base["execution_id"] = eid
            _append_bootstrap_trace(str(eid), {"kind": "ledger_phase", "phase": "CREATED"})
            timings = ctx.get("bootstrap_timings_ms")
            if timings:
                _append_bootstrap_trace(
                    str(eid),
                    {"kind": "bootstrap_timings_ms", "timings_ms": timings},
                )
    else:
        raise RuntimeError(f"Unsupported activity mode: {cfg.mode}")

    _WORKFLOW_ADAPTERS[workflow_id] = adapter
    activity.logger.info("cursor session ready session_id=%s mode=%s", adapter.session_id(), cfg.mode)
    return {"session_id": adapter.session_id()}


def _bugbot_loop(adapter: Any, payload: dict[str, Any]) -> BugbotRepairLoop:
    cfg = _require_config()
    base_ref = (payload.get("base") or cfg.base or REVIEW_BASE_REF).strip()
    mock_mode = cfg.mode == "mock"
    # Mock scenarios match exact REVIEW_PROMPT text; skip live git preflight.
    repo_path = None if mock_mode else cfg.repo_path
    return BugbotRepairLoop(
        adapter,
        repo_path=repo_path,
        base_ref=base_ref,
        head=payload.get("head") or cfg.head,
        mock_mode=mock_mode,
    )


@activity.defn(name="execute_bugbot_review")
async def execute_bugbot_review(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    review_number = config.get("review_number", "?")
    activity.logger.info(
        "starting bugbot review #%s session_id=%s model=%s base=%s",
        review_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="review") if _MODEL_CONFIG else None,
        config.get("base"),
    )
    result = await asyncio.to_thread(_bugbot_loop(adapter, config).run_single_review)
    activity.logger.info(
        "finished bugbot review #%s clean=%s repairable=%s excluded=%s git_preflight_ok=%s fingerprint=%s",
        review_number,
        result.clean,
        len(result.repairable_findings),
        len(result.excluded_findings),
        result.git_preflight_ok,
        result.fingerprint,
    )
    return result.to_dict()


@activity.defn(name="execute_opsx_repair")
async def execute_opsx_repair(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    repair_number = config.get("repair_number", "?")
    activity.logger.info(
        "starting opsx repair #%s session_id=%s model=%s base=%s",
        repair_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="repair") if _MODEL_CONFIG else None,
        config.get("base"),
    )
    result = await asyncio.to_thread(_bugbot_loop(adapter, config).run_single_repair)
    activity.logger.info(
        "finished opsx repair #%s completed=%s bug_batch_clear=%s uncertain=%s",
        repair_number,
        result.completed,
        result.bug_batch_clear,
        result.uncertain,
    )
    return result.to_dict()


def _security_loop(adapter: Any, payload: dict[str, Any]) -> ReviewRepairLoop:
    cfg = _require_config()
    mock_mode = cfg.mode == "mock"
    lane = make_security_lane(
        base=(payload.get("base") or cfg.base or REVIEW_BASE_REF),
    )
    return ReviewRepairLoop(
        adapter,
        lane=lane,
        repo_path=None if mock_mode else cfg.repo_path,
        base_ref=(payload.get("base") or cfg.base or REVIEW_BASE_REF),
        head=payload.get("head") or cfg.head,
        mock_mode=mock_mode,
    )


@activity.defn(name="execute_security_review")
async def execute_security_review(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    review_number = config.get("review_number", "?")
    activity.logger.info(
        "starting security review #%s session_id=%s model=%s",
        review_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="review") if _MODEL_CONFIG else None,
    )
    result = await asyncio.to_thread(_security_loop(adapter, config).run_single_review)
    activity.logger.info(
        "finished security review #%s clean=%s repairable=%s excluded=%s fingerprint=%s",
        review_number,
        result.clean,
        len(result.repairable_findings),
        len(result.excluded_findings),
        result.fingerprint,
    )
    return result.to_dict()


@activity.defn(name="execute_security_repair")
async def execute_security_repair(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    repair_number = config.get("repair_number", "?")
    activity.logger.info(
        "starting security repair #%s session_id=%s model=%s",
        repair_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="repair") if _MODEL_CONFIG else None,
    )
    result = await asyncio.to_thread(_security_loop(adapter, config).run_single_repair)
    activity.logger.info(
        "finished security repair #%s completed=%s bug_batch_clear=%s uncertain=%s",
        repair_number,
        result.completed,
        result.bug_batch_clear,
        result.uncertain,
    )
    return result.to_dict()


def _project_bug_hunt_loop(adapter: Any, payload: dict[str, Any]) -> ReviewRepairLoop:
    cfg = _require_config()
    mock_mode = cfg.mode == "mock"
    lane = _resolve_lane(payload)
    return ReviewRepairLoop(
        adapter,
        lane=lane,
        repo_path=None if mock_mode else cfg.repo_path,
        base_ref=(payload.get("base") or cfg.base or REVIEW_BASE_REF),
        head=payload.get("head") or cfg.head,
        change=payload.get("change") or cfg.change,
        mock_mode=mock_mode,
    )


@activity.defn(name="execute_project_bug_hunt")
async def execute_project_bug_hunt(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    review_number = config.get("review_number", "?")
    activity.logger.info(
        "starting project-bug-hunt #%s session_id=%s model=%s change=%s",
        review_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="review") if _MODEL_CONFIG else None,
        config.get("change"),
    )
    result = await _run_blocking_with_heartbeat(
        _project_bug_hunt_loop(adapter, config).run_single_review,
        details={
            "step": "execute_project_bug_hunt",
            "phase": "project_bug_hunt",
            "review_number": review_number,
            "change": config.get("change"),
        },
    )
    activity.logger.info(
        "finished project-bug-hunt #%s clean=%s repairable=%s excluded=%s fingerprint=%s",
        review_number,
        result.clean,
        len(result.repairable_findings),
        len(result.excluded_findings),
        result.fingerprint,
    )
    return result.to_dict()


@activity.defn(name="execute_project_bug_hunt_orch")
async def execute_project_bug_hunt_orch(config: dict[str, Any]) -> dict[str, Any]:
    adapter = _get_or_restore_adapter(config)
    repair_number = config.get("repair_number", "?")
    activity.logger.info(
        "starting project-bug-hunt orch #%s session_id=%s model=%s change=%s",
        repair_number,
        adapter.session_id(),
        _MODEL_CONFIG.resolve_cli_model(step="repair") if _MODEL_CONFIG else None,
        config.get("change"),
    )
    result = await _run_blocking_with_heartbeat(
        _project_bug_hunt_loop(adapter, config).run_single_repair,
        details={
            "step": "execute_project_bug_hunt_orch",
            "phase": "project_bug_hunt",
            "repair_number": repair_number,
            "change": config.get("change"),
        },
    )
    activity.logger.info(
        "finished project-bug-hunt orch #%s completed=%s bug_batch_clear=%s repaired=%s uncertain=%s",
        repair_number,
        result.completed,
        result.bug_batch_clear,
        result.repaired_this_batch,
        result.uncertain,
    )
    return result.to_dict()


@activity.defn(name="execute_commit_materialize")
async def execute_commit_materialize(config: dict[str, Any]) -> dict[str, Any]:
    """Authorized post-repair `/opsx-commit-all GO` hop. Never git-writes itself."""
    from commit_materialization import materialize_if_needed

    cfg = _require_config()
    mock_mode = cfg.mode == "mock"
    repair_payload = config.get("repair") or {}
    repair = repair_result_from_dict(repair_payload)
    if mock_mode:
        adapter = None
    else:
        from authorized_commit_all import make_authorized_commit_all_adapter

        adapter = make_authorized_commit_all_adapter(
            cfg.repo_path,
            workflow_key=f"{_workflow_key()}:commit-all",
            model_config=_MODEL_CONFIG,
        )
    repo_path = None if mock_mode else cfg.repo_path
    activity.logger.info(
        "starting commit materialize session_id=%s mock=%s repaired=%s",
        None if adapter is None else adapter.session_id(),
        mock_mode,
        repair.repaired_this_batch,
    )
    result = await _run_blocking_with_heartbeat(
        lambda: materialize_if_needed(
            repo_path=repo_path,
            adapter=adapter,
            repair=repair,
            mock_mode=mock_mode,
        ),
        details={"step": "commit-all"},
    )
    activity.logger.info(
        "finished commit materialize ok=%s status=%s transitioned=%s head=%s",
        result.ok,
        result.status,
        result.candidate_transitioned,
        result.head_after,
    )
    return result.to_dict()


@activity.defn(name="parse_bugbot_result")
async def parse_bugbot_result_activity(raw_output: str) -> dict[str, Any]:
    return parse_bugbot_result(raw_output).to_dict()


@activity.defn(name="parse_opsx_bug_fix_result")
async def parse_opsx_bug_fix_result_activity(raw_output: str) -> dict[str, Any]:
    return parse_opsx_bug_fix_result(raw_output).to_dict()


def review_result_from_dict(data: dict[str, Any]) -> ReviewRunResult:
    actionable = data.get("actionable_findings", [])
    repairable = data.get("repairable_findings", actionable)
    excluded = data.get("excluded_findings", [])
    return ReviewRunResult(
        completed=data["completed"],
        clean=data.get("clean"),
        findings=data.get("findings", []),
        actionable_findings=actionable,
        repairable_findings=repairable,
        excluded_findings=excluded,
        fingerprint=data.get("fingerprint"),
        raw_output=data.get("raw_output", ""),
        transient_error=data.get("transient_error", False),
        git_preflight_ok=data.get("git_preflight_ok", False),
        candidate_sha=data.get("candidate_sha"),
    )


def repair_result_from_dict(data: dict[str, Any]) -> RepairRunResult:
    return RepairRunResult(
        completed=data["completed"],
        bug_batch_clear=data.get("bug_batch_clear"),
        unresolved=data.get("unresolved", []),
        running_worker=data.get("running_worker"),
        raw_output=data.get("raw_output", ""),
        uncertain=data.get("uncertain", False),
        repaired_this_batch=data.get("repaired_this_batch"),
    )


def _fresh_real_adapter() -> Any:
    """Fresh SDK agent per hop — verification must not rely on prior chat."""
    cfg = _require_config()
    hop_key = f"{_workflow_key()}:verify:{uuid.uuid4().hex[:12]}"
    adapter = make_sdk_cursor_adapter(
        cfg.repo_path,
        workflow_key=hop_key,
        model_config=_MODEL_CONFIG,
    )
    adapter.bootstrap_agent()
    return adapter


def _verification_change(payload: dict[str, Any]) -> str:
    cfg = _require_config()
    change = (payload.get("change") or cfg.change or "").strip()
    if not change:
        raise ValueError("verification activities require change=")
    return change


@activity.defn(name="ensure_verification_context")
async def ensure_verification_context(config: dict[str, Any]) -> dict[str, Any]:
    """Validate bounded-verify plan carrier; no Cursor session as authority."""
    from verification_plan_bridge import resolve_verification_plan_context

    cfg = _require_config()
    change = _verification_change(config)
    activity.logger.info(
        "ensure_verification_context change=%s mode=%s carrier=bounded-verify",
        change,
        cfg.mode,
    )
    if cfg.mode == "mock":
        return {
            "status": "OK",
            "change": change,
            "plan_id": None,
            "ledger_path": None,
            "ledger_present": False,
            "VERIFICATION_PLAN_CARRIER": "bounded-verify/plan.json",
            "VERIFICATION_SESSION_DEPENDENCY": "NONE",
            "mock": True,
        }
    ctx = resolve_verification_plan_context(cfg.repo_path, change)
    activity.logger.info(
        "ensure_verification_context resolved status=%s plan_id=%s ledger=%s",
        ctx.status,
        ctx.plan_id,
        ctx.ledger_present,
    )
    return ctx.to_activity_payload()


@activity.defn(name="execute_next_check")
async def execute_next_check(config: dict[str, Any]) -> dict[str, Any]:
    """Run /opsx-next-check in a fresh Cursor context; ledger is authority."""
    from verification_engine import build_next_check_prompt, classify_next_check
    from verification_mock import get_mock_harness

    cfg = _require_config()
    change = _verification_change(config)
    number = config.get("next_check_number", "?")
    workflow_id = _workflow_key()

    if cfg.mode == "mock":
        scenario = (
            config.get("mock_scenario") or cfg.mock_scenario or "verification-all-pass"
        )
        harness = get_mock_harness(
            workflow_id, scenario=scenario, change=change, repo=cfg.repo_path
        )
        result = await asyncio.to_thread(harness.run_next_check)
        activity.logger.info(
            "mock next-check #%s status=%s progress=%s/%s",
            number,
            result.status,
            result.progress_pass,
            result.progress_total,
        )
        return result.to_dict()

    if cfg.mode != "real":
        raise RuntimeError(f"Unsupported activity mode: {cfg.mode}")

    prompt = build_next_check_prompt(change)
    activity.logger.info(
        "starting next-check #%s change=%s model=%s fresh_session=1",
        number,
        change,
        _MODEL_CONFIG.resolve_cli_model(step="next_check") if _MODEL_CONFIG else None,
    )
    try:
        adapter = await asyncio.to_thread(_fresh_real_adapter)
        invocation = await _run_blocking_with_heartbeat(
            lambda: adapter.execute_prompt(prompt, step="next_check"),
            details={"step": "next_check", "number": number, "change": change},
        )
        raw = invocation.raw_output or ""
        completed = invocation.exit_code == 0 or bool(raw.strip())
        exit_code = invocation.exit_code
    except Exception as exc:  # noqa: BLE001 — surface as technical failure
        activity.logger.warning("next-check #%s technical failure: %s", number, exc)
        return classify_next_check(
            repo=cfg.repo_path,
            change=change,
            raw_output=f"STATUS: NEXT_CHECK_TECHNICAL_FAILURE\n{exc}",
            completed=False,
            agent_exit_code=1,
        ).to_dict()

    result = classify_next_check(
        repo=cfg.repo_path,
        change=change,
        raw_output=raw,
        completed=completed,
        agent_exit_code=exit_code,
    )
    activity.logger.info(
        "finished next-check #%s status=%s current=%s progress=%s/%s",
        number,
        result.status,
        result.current_required_check_id,
        result.progress_pass,
        result.progress_total,
    )
    return result.to_dict()


@activity.defn(name="execute_assigned_check")
async def execute_assigned_check(config: dict[str, Any]) -> dict[str, Any]:
    """Run exact CHECK_ASSIGNMENT executor in a fresh Cursor context."""
    from verification_engine import (
        AssignedCheckResult,
        build_execute_prompt,
        inspect_expected_result,
        validate_assignment_for_execute,
    )
    from verification_mock import get_mock_harness

    cfg = _require_config()
    change = _verification_change(config)
    assignment = config.get("assignment") or {}
    number = config.get("executor_number", "?")
    workflow_id = _workflow_key()

    bounded_target = bool(assignment.get("bounded_verify_target"))
    if bounded_target:
        import sys
        from pathlib import Path as _Path

        bv_root = _Path(cfg.repo_path) / ".cursor" / "pipeline" / "bounded-verify"
        if str(bv_root) not in sys.path:
            sys.path.insert(0, str(bv_root))
        from bounded_verify_runtime_preflight import (  # noqa: PLC0415
            PreflightOutcome,
            preflight_static_tools,
        )

        preflight = preflight_static_tools(require_temporal=cfg.mode == "real")
        if preflight.outcome != PreflightOutcome.READY:
            return AssignedCheckResult(
                completed=False,
                uncertain=False,
                durable_result_found=False,
                durable_result_status=None,
                expected_result_path=assignment.get("expected_result_path"),
                executor=assignment.get("executor"),
                required_check_id=assignment.get("required_check_id"),
                raw_output=(
                    "STATUS: VERIFICATION_CHECK_BLOCKED\n"
                    f"REASON: RUNTIME_PREFLIGHT_{preflight.outcome.value}\n"
                    f"DETAIL: {preflight.message}\n"
                ),
                started=False,
            ).to_dict()

    if not assignment.get("executor") or (
        not bounded_target and not assignment.get("expected_result_path")
    ):
        return AssignedCheckResult(
            completed=False,
            uncertain=True,
            durable_result_found=False,
            durable_result_status=None,
            expected_result_path=assignment.get("expected_result_path"),
            executor=assignment.get("executor"),
            required_check_id=assignment.get("required_check_id"),
            raw_output="assignment missing executor or expected_result_path",
            started=False,
        ).to_dict()

    if cfg.mode == "mock":
        scenario = (
            config.get("mock_scenario") or cfg.mock_scenario or "verification-all-pass"
        )
        harness = get_mock_harness(
            workflow_id, scenario=scenario, change=change, repo=cfg.repo_path
        )
        result = await asyncio.to_thread(harness.run_assigned_check, assignment)
        activity.logger.info(
            "mock assigned-check #%s executor=%s durable=%s status=%s",
            number,
            result.executor,
            result.durable_result_found,
            result.durable_result_status,
        )
        return result.to_dict()

    if cfg.mode != "real":
        raise RuntimeError(f"Unsupported activity mode: {cfg.mode}")

    missing_assignment = validate_assignment_for_execute(assignment)
    if missing_assignment:
        activity.logger.warning(
            "assigned-check #%s blocked: incomplete CHECK_ASSIGNMENT missing=%s",
            number,
            missing_assignment,
        )
        return AssignedCheckResult(
            completed=False,
            uncertain=False,
            durable_result_found=False,
            durable_result_status=None,
            expected_result_path=assignment.get("expected_result_path"),
            executor=assignment.get("executor"),
            required_check_id=assignment.get("required_check_id"),
            raw_output=(
                "STATUS: VERIFICATION_CHECK_BLOCKED\n"
                "REASON: ASSIGNMENT_INCOMPLETE\n"
                f"MISSING_FIELDS: {','.join(missing_assignment)}\n"
            ),
            started=False,
        ).to_dict()

    prompt = build_execute_prompt(assignment, repo=cfg.repo_path)
    activity.logger.info(
        "starting assigned-check #%s executor=%s check=%s model=%s fresh_session=1",
        number,
        assignment.get("executor"),
        assignment.get("required_check_id"),
        (
            _MODEL_CONFIG.resolve_cli_model(step="verification_executor")
            if _MODEL_CONFIG
            else None
        ),
    )

    raw = ""
    exit_code = None
    started = False
    completed = False
    uncertain = False
    try:
        adapter = await asyncio.to_thread(_fresh_real_adapter)
        started = True
        invocation = await _run_blocking_with_heartbeat(
            lambda: adapter.execute_prompt(prompt, step="verification_executor"),
            details={
                "step": "verification_executor",
                "number": number,
                "required_check_id": assignment.get("required_check_id"),
            },
        )
        raw = invocation.raw_output or ""
        exit_code = invocation.exit_code
        completed = exit_code == 0
        if exit_code is None:
            uncertain = True
    except Exception as exc:  # noqa: BLE001
        uncertain = True
        raw = f"executor exception: {exc}"
        activity.logger.warning("assigned-check #%s exception: %s", number, exc)

    durable = inspect_expected_result(assignment, repo=cfg.repo_path)
    if durable is not None:
        uncertain = False
        completed = True
    elif uncertain or not completed:
        if started and not durable:
            uncertain = True
    # Nested Composer stdout must not masquerade as PASS without durable result.
    if started and durable is None and "BROWSER_CHECK_PASS" in raw:
        uncertain = True
        raw = (
            f"{raw.rstrip()}\n"
            "STATUS: EXECUTOR_COMPLETION_UNCERTAIN\n"
            "REASON: DURABLE_RESULT_MISSING\n"
        )

    browser_release = None
    if started and durable is not None:
        from owner_browser_check_gate import (
            is_browser_check_assignment,
            run_browser_check_release,
        )

        if is_browser_check_assignment(assignment):
            browser_release = run_browser_check_release(
                repo=cfg.repo_path,
                change=change,
                assignment=assignment,
                durable_result_status=(durable or {}).get("STATUS") if durable else None,
            )

    result = AssignedCheckResult(
        completed=completed,
        uncertain=uncertain and durable is None,
        durable_result_found=durable is not None,
        durable_result_status=(durable or {}).get("STATUS") if durable else None,
        expected_result_path=assignment.get("expected_result_path"),
        executor=assignment.get("executor"),
        required_check_id=assignment.get("required_check_id"),
        raw_output=raw,
        started=started,
        exit_code=exit_code,
        fresh_session_id=None,
        owner_browser_check_release=browser_release,
    )
    activity.logger.info(
        "finished assigned-check #%s durable=%s status=%s uncertain=%s browser_release=%s",
        number,
        result.durable_result_found,
        result.durable_result_status,
        result.uncertain,
        browser_release is not None and not (browser_release or {}).get("skipped"),
    )
    return result.to_dict()
