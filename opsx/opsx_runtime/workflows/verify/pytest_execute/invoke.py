"""Публичный API: ``run_pytest_execute`` — collect, harness repair loop, pytest run."""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.verify.pytest_run_health import (
    CollectHealthResult,
    run_collect_only,
    scope_key_from_modules,
)
from workflows.verify.gate_repair.pytest_gate_timeout import (
    QUALITY_GATE_PER_TEST_SECONDS,
    parse_collected_test_count,
    resolve_collect_phase_timeout_seconds,
    resolve_pytest_gate_timeout_seconds,
)
from workflows.verify.gate_repair.schemas import GateCheckResult
from workflows.verify.pytest_execute.schemas import (
    CollectProof,
    PytestExecuteRequest,
    PytestExecuteResult,
)

__all__ = [
    "run_pytest_execute",
    "run_pytest_execute_sync",
    "pytest_execute_state_patch",
    "result_to_gate_check",
]


def result_to_gate_check(result: PytestExecuteResult) -> GateCheckResult:
    """Маппинг в ``QualityGateRawBundle.pytest``."""
    if result.gate_check is not None:
        return result.gate_check
    return GateCheckResult(
        check_id="pytest",
        command=[],
        exit_code=result.collect_proof.collect_exit_code if not result.collect_proof.collect_ok else 2,
        duration_ms=0,
        stdout_tail=result.collect_proof.collect_tail[-3500:],
        stderr_tail="",
        error=result.detail or None,
    )


def pytest_execute_state_patch(result: PytestExecuteResult) -> dict[str, Any]:
    """Поля GraphState (F-01)."""
    scope = result.scope_key
    by_scope = {
        scope: {
            "collect_ok": result.collect_proof.collect_ok,
            "collect_exit_code": result.collect_proof.collect_exit_code,
            "collect_tail": result.collect_proof.collect_tail,
            "attempts_used": result.collect_proof.attempts_used,
            "finished_at": "pytest_execute",
        }
    }
    paths = [p for p in scope.split("|") if p and p != "empty"]
    patch: dict[str, Any] = {
        "last_pytest_execute_by_scope": by_scope,
        "pytest_execute_finished_scopes": paths,
    }
    if result.owner_attention:
        patch["owner_attention"] = result.owner_attention
    return patch


async def _harness_commit(
    state: GraphState,
    *,
    repair_adapter: Any | None,
) -> dict[str, Any]:
    """Unified CommitAgent hop после harness FIXED (state уже с ``dispatcher_current_item``)."""
    if repair_adapter is None or not state.get("verify_finding_fix"):
        return {}
    from workflows.verify.dispatch_cycle.commit_node import run_verify_finding_commit

    return await run_verify_finding_commit(state, repair_adapter=repair_adapter)


async def harness_repair_and_commit(
    *,
    state: GraphState,
    collect: CollectHealthResult,
    modules: list[str],
    harness_agent: Any,
    repair_adapter: Any | None,
) -> dict[str, Any]:
    """Один hop ``test_harness_repair`` + optional commit на ``mini_state``."""
    from agents.verify.test_harness_repair.input_pack import build_harness_repair_input_pack
    from workflows.verify.pytest_execute.nodes import build_harness_repair_item

    item = build_harness_repair_item(state, collect, modules)
    pack = build_harness_repair_input_pack(
        repository_path=str(state.get("repository_path") or state.get("workspace") or "."),
        item=item,
        state=state,
        collect=collect,
    )
    mini_state: GraphState = {
        **state,
        "dispatcher_current_item": item,
        "test_harness_repair_input_pack": pack.model_dump(mode="json"),
    }
    patch = await harness_agent.run(mini_state)  # type: ignore[no-any-return]
    mini_state.update(patch)
    tr_raw = patch.get("test_harness_repair_result")
    status = str(tr_raw.get("status", "")) if isinstance(tr_raw, dict) else ""
    if status == "FIXED":
        patch.update(await _harness_commit(mini_state, repair_adapter=repair_adapter))
    return patch


def _run_pytest_after_collect(
    repo_root: Path,
    modules: list[str],
    *,
    timeout_seconds: float,
    scope_key: str,
    test_count: int,
) -> tuple[GateCheckResult, str]:
    """Pytest после collect: общий бюджет ``timeout_seconds`` и stall ``QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS``."""
    from workflows.verify.gate_repair.pytest_subprocess_streaming import (
        run_pytest_gate_with_progress,
    )
    from workflows.verify.gate_repair.runner import _resolve_poetry
    from workflows.verify.pytest_workflow_argv import (
        build_workflow_pytest_argv,
        default_workflow_pytest_paths,
    )

    prefix = _resolve_poetry(repo_root)
    if modules:
        paths = [
            str((resource_path(repo_root, m)).resolve() if not Path(m).is_absolute() else Path(m))
            for m in modules
        ]
    else:
        paths = default_workflow_pytest_paths(repo_root)
    cmd = build_workflow_pytest_argv(
        repo_root,
        paths=paths,
        poetry_prefix=prefix,
        quiet=False,
    )
    return run_pytest_gate_with_progress(
        cmd,
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
        scope=scope_key,
        test_count=test_count,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
    )


async def run_pytest_execute(
    repo_root: Path,
    request: PytestExecuteRequest,
    *,
    state: GraphState | None = None,
    harness_repair_agent: Any | None = None,
    repair_adapter: Any | None = None,
) -> PytestExecuteResult:
    """Collect-loop + optional harness repair + pytest run (graph-safe async).

    Блокирующие collect/pytest subprocess выполняются в ``asyncio.to_thread``;
    отмена coroutine не останавливает subprocess мгновенно (действует gate timeout).
    """
    modules = list(request.module_paths)
    if request.scope_kind == "FULL_SUITE" and not modules:
        tests = resource_path(repo_root, "tests")
        modules = [str(tests)] if tests.is_dir() else []
    scope_key = scope_key_from_modules(
        [str(Path(m).as_posix()) if m else m for m in modules]
    )
    caller_cap = request.timeout_seconds
    collect_timeout = resolve_collect_phase_timeout_seconds(caller_cap_seconds=caller_cap)
    harness_attempts = 0
    files_changed: list[str] = []
    graph_state: GraphState = dict(state or {})

    collect: CollectHealthResult | None = None
    while True:
        collect = await asyncio.to_thread(
            run_collect_only,
            repo_root,
            modules,
            timeout_seconds=collect_timeout,
        )
        if collect.ok:
            break
        if harness_repair_agent is None:
            break
        if harness_attempts >= request.max_harness_attempts:
            break
        harness_attempts += 1
        patch = await harness_repair_and_commit(
            state=graph_state,
            collect=collect,
            modules=modules,
            harness_agent=harness_repair_agent,
            repair_adapter=repair_adapter,
        )
        graph_state.update(patch)
        fix = patch.get("verify_finding_fix")
        if isinstance(fix, dict) and isinstance(fix.get("files_changed"), list):
            files_changed.extend(str(x) for x in fix["files_changed"])
        tr_raw = patch.get("test_harness_repair_result")
        status = str(tr_raw.get("status", "")) if isinstance(tr_raw, dict) else ""
        if status == "BLOCKED":
            proof = CollectProof(
                scope_key=scope_key,
                collect_ok=False,
                collect_exit_code=collect.exit_code,
                collect_tail=collect.log_tail,
                attempts_used=harness_attempts,
            )
            return PytestExecuteResult(
                status="PAUSE",
                scope_key=scope_key,
                collect_proof=proof,
                harness_attempts=harness_attempts,
                files_changed=sorted(set(files_changed)),
                detail="harness_repair_blocked",
            )

    assert collect is not None
    proof = CollectProof(
        scope_key=scope_key,
        collect_ok=collect.ok,
        collect_exit_code=collect.exit_code,
        collect_tail=collect.log_tail,
        attempts_used=harness_attempts,
    )
    if (
        not collect.ok
        and harness_repair_agent is not None
        and harness_attempts >= request.max_harness_attempts
    ):
        attn = f"HARNESS_COLLECTION_BLOCKER: scope={scope_key}"
        return PytestExecuteResult(
            status="PAUSE",
            scope_key=scope_key,
            collect_proof=proof,
            harness_attempts=harness_attempts,
            files_changed=sorted(set(files_changed)),
            detail="harness_repair_cap_exhausted",
            owner_attention=attn,
        )
    if request.collect_only and not collect.ok:
        return PytestExecuteResult(
            status="FAIL",
            scope_key=scope_key,
            collect_proof=proof,
            harness_attempts=harness_attempts,
            files_changed=sorted(set(files_changed)),
            detail="collect_failed",
        )
    if request.collect_only:
        run_only_status: str = "PASS" if collect.ok else "FAIL"
        return PytestExecuteResult(
            status=run_only_status,  # type: ignore[arg-type]
            scope_key=scope_key,
            collect_proof=proof,
            harness_attempts=harness_attempts,
            files_changed=sorted(set(files_changed)),
            detail="collect_only",
        )

    parsed_count = parse_collected_test_count(collect.log_tail) or 0
    from agents.verify.test_duration.timeout_policy import personal_timeout_extra_seconds

    run_timeout = (
        resolve_pytest_gate_timeout_seconds(
            parsed_count,
            caller_cap_seconds=caller_cap,
        )
        + personal_timeout_extra_seconds(repo_root)
    )
    if state is not None:
        from workflows.verify.routing_log import log_quality_gates_pytest_start

        log_quality_gates_pytest_start(
            graph_state,
            scope=request.scope_kind if request.scope_kind != "MODULES" else scope_key,
            timeout_s=run_timeout,
            test_count=parsed_count,
            per_test_s=QUALITY_GATE_PER_TEST_SECONDS,
        )
    gate, full_log = await asyncio.to_thread(
        _run_pytest_after_collect,
        repo_root,
        modules,
        timeout_seconds=run_timeout,
        scope_key=scope_key,
        test_count=parsed_count,
    )
    run_status: str = "PASS" if gate.exit_code == 0 and gate.error is None else "FAIL"
    return PytestExecuteResult(
        status=run_status,  # type: ignore[arg-type]
        scope_key=scope_key,
        collect_proof=proof,
        gate_check=gate,
        harness_attempts=harness_attempts,
        files_changed=sorted(set(files_changed)),
        detail=f"pytest_run exit={gate.exit_code}",
        run_log=full_log,
    )


def run_pytest_execute_sync(
    repo_root: Path,
    request: PytestExecuteRequest,
    *,
    state: GraphState | None = None,
    harness_repair_agent: Any | None = None,
    repair_adapter: Any | None = None,
) -> PytestExecuteResult:
    """Sync entry: только когда нет running event loop (CLI / peripheral)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            run_pytest_execute(
                repo_root,
                request,
                state=state,
                harness_repair_agent=harness_repair_agent,
                repair_adapter=repair_adapter,
            ),
        )
    raise RuntimeError(
        "run_pytest_execute_sync cannot run inside an active event loop; "
        "use await run_pytest_execute(...) in LangGraph nodes",
    )
