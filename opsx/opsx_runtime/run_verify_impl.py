"""CLI: parent LangGraph ``START → implementation → verify`` (composition root).

Implementation пропускается целиком, если ``super-implement/RUN.json`` уже
``APPLY_COMPLETE`` (durable receipt).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
from contextlib import AsyncExitStack
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from agents.adapters.adapter_stream_constants import (  # noqa: E402
    CURSOR_HOP_HARD_DEADLINE_SECONDS,
)
from agents.adapters.bridge_runtime_port import open_bridge_runtime  # noqa: E402
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.provider_registry import build_review_adapter  # noqa: E402
from runtime_bootstrap import ensure_provider_wheel_importable  # noqa: E402

ensure_provider_wheel_importable()
ensure_adapter_factories_registered()
from workflows.main_graph import (  # noqa: E402
    build_gate_repair_hop_agents,
    build_implementation_hop_agents,
    build_verify_hop_agents,
)
from workflows.repair.repair_adapter_port import RepairAdapter  # noqa: E402
from agents.verify.bugbot.agent import BugBotAgent  # noqa: E402
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent  # noqa: E402
from agents.verify.security_review.agent import SecurityReviewAgent  # noqa: E402
from workflows.artifact_paths import receipts_dir, run_log_path  # noqa: E402
from workflows.verify.persist import persist_impl_verify_artifacts  # noqa: E402
from workflows.stop_errors import OpsxStageStop, format_stage_stop
from workflows.verify.cli_outcome import (
    format_verify_impl_security_pass_cli_status,
    raise_if_implementation_lane_terminal,
    raise_unless_security_pass,
)  # noqa: E402
from workflows.checkpoint_resume import (
    WorkflowResumeScope,
    default_run_dir,
    durable_langgraph_checkpoint_enabled,
    reset_row_walk_journal_repair_attempts,
    reset_verify_impl_checkpoints,
)
from workflows.repo_paths import graph_repo_path_patch, resolve_opsx_repository_root
from workflows.main import run_implement_then_verify_workflow  # noqa: E402
from workflows.workflow_logging import ensure_workflow_stderr_logging  # noqa: E402
from workflows.implementation.implementation_dispatch_ledger import (  # noqa: E402
    ImplementationDispatchLedger,
    dispatch_ledger_path,
)
from workflows.implementation.adapter_run_reconcile import (  # noqa: E402
    AdapterRunDisposition,
    reconcile_verify_impl_adapter_runs,
    store_reconcile_report,
)

_CONFIGS_ROOT = resource_path(REPO_ROOT, "opsx", "configs")
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))


async def execute(
    change_id: str,
    *,
    repo_root: Path,
    timeout_seconds: float = CURSOR_HOP_HARD_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Parent workflow: implementation (или receipt skip) → verify."""
    ensure_workflow_stderr_logging()
    repo_root = resolve_opsx_repository_root(repo_root)
    session_id = uuid.uuid4().hex[:12]
    stream_log = run_log_path(repo_root, change_id, session_id)
    stream_log.parent.mkdir(parents=True, exist_ok=True)

    # Composition root owns bridge lifecycle; AsyncExitStack is the
    # multi-bridge equivalent of try/finally and closes on every exit path.
    async with AsyncExitStack() as bridge_stack:
        # Stable per-change recovery scope: retries can reconcile original SDK IDs.
        # A fresh per-invocation directory would orphan unfinished row runs.
        try:
            bridge = await bridge_stack.enter_async_context(
                open_bridge_runtime(
                    repo_root,
                    store_backend="sqlite",
                    store_scope=f"verify_impl:{change_id}",
                ),
            )
        except BaseException as exc:
            from agents.abc_agent import raise_cursor_infra_failure

            raise_cursor_infra_failure(exc)
        reconcile_report = await reconcile_verify_impl_adapter_runs(
            change_id,  
            repo_root,
            bridge,
        )
        store_reconcile_report(session_id, reconcile_report)
        ledger = ImplementationDispatchLedger(dispatch_ledger_path(change_id, repo_root=repo_root))
        for row in reconcile_report.rows:
            if row.disposition == AdapterRunDisposition.FINISHED:
                terminal = str(row.get_run_status or "FINISHED")
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status=terminal,
                )
            elif row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status="ORPHAN",
                )
        _reconcile_log = logging.getLogger(__name__)
        if reconcile_report.error:
            _reconcile_log.warning(
                "verify_impl sdk_reconcile error=%s change=%s index=%s",
                reconcile_report.error,
                change_id,
                reconcile_report.index_db,
            )
        elif reconcile_report.rows:
            summary = ", ".join(
                f"{r.record.run_id}:{r.disposition.value}" for r in reconcile_report.rows[:5]
            )
            _reconcile_log.info(
                "verify_impl sdk_reconcile change=%s rows=%s %s",
                change_id,
                len(reconcile_report.rows),
                summary,
            )
        review_adapter = build_review_adapter(
            timeout_seconds=timeout_seconds,
            allow_git_shell=False,
            bridge_runtime=bridge,
        )
        apply_adapter = RepairAdapter(
            timeout_seconds=timeout_seconds, bridge_runtime=bridge,
        )
        (
            impl_agent,
            scenario_coverage_agent,
            coverage_activation_agent,
            apply_agent,
            scenario_test_repair_agent,
            archive_verify_agent,
        ) = build_verify_hop_agents(
            review_adapter=review_adapter,
            apply_adapter=apply_adapter,
        )
        (
            group_error_agent,
            repair_order_agent,
            diagnostic_agent,
            static_triage_agent,
            typing_repair_agent,
            test_triage_agent,
            test_repair_agent,
            _harness_repair_agent,
            _pytest_classifier_agent,
        ) = build_gate_repair_hop_agents(
            review_adapter=review_adapter,
            repair_adapter=apply_adapter,
        )
        project_bug_hunt_agent = ProjectBugHuntAgent(review_adapter)
        bugbot_agent = BugBotAgent(review_adapter)
        security_review_agent = SecurityReviewAgent(review_adapter)
        group_error_stream_log = stream_log
        repair_order_stream_log = stream_log
        diagnostic_stream_log = stream_log
        from workflows.repair.repair_adapter_port import default_hop_model_selection

        dry_run = os.environ.get("OPSX_IMPLEMENTATION_DRY_RUN", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

        budget_dir = receipts_dir(repo_root, change_id)
        budget_dir.mkdir(parents=True, exist_ok=True)
        initial_state = {
            **graph_repo_path_patch({}, repo_root),
            "change_id": change_id,
            "run_uuid": session_id,
            "live_execution_authorized": not dry_run,
            "model": default_hop_model_selection(),
            "budget_output_dir": str(budget_dir),
            "mission_id": f"verify-impl-{change_id}",
            "stream_log": str(stream_log),
            "stream_label": f"verify-impl-{change_id}",
            "group_error_stream_log": str(group_error_stream_log),
            "group_error_stream_label": f"group-error-{change_id}",
            "repair_order_stream_log": str(repair_order_stream_log),
            "repair_order_stream_label": f"repair-order-{change_id}",
            "diagnostic_stream_log": str(diagnostic_stream_log),
            "diagnostic_stream_label": f"diagnostic-{change_id}",
        }
        implementation_hop_agents = build_implementation_hop_agents(
            apply_adapter=apply_adapter,
        )
        resume_scope: WorkflowResumeScope | None = None
        if durable_langgraph_checkpoint_enabled():
            resume_scope = WorkflowResumeScope(kind="verify_impl", change_id=change_id)
        from bug_log.bind import bind_bug_journal
        from bug_log.context import bind_current_journal, reset_current_journal
        from bug_log.file_journal import open_run_journal, require_live_journal
        from bug_log.projector import BugJournalProjector

        bug_journal = open_run_journal(
            repo_root=repo_root,
            run_id=session_id,
            change_id=change_id,
            change_scope="CHANGE",
            stream_log=str(stream_log),
            mission_id=f"verify-impl-{change_id}",
        )
        require_live_journal(bug_journal, live=not dry_run)
        for agent in (
            impl_agent,
            scenario_coverage_agent,
            coverage_activation_agent,
            apply_agent,
            scenario_test_repair_agent,
            archive_verify_agent,
            group_error_agent,
            repair_order_agent,
            diagnostic_agent,
            static_triage_agent,
            typing_repair_agent,
            test_triage_agent,
            test_repair_agent,
            project_bug_hunt_agent,
            bugbot_agent,
            security_review_agent,
        ):
            bind_bug_journal(agent, bug_journal)
        journal_token = bind_current_journal(bug_journal)
        try:
            try:
                final_state = await run_implement_then_verify_workflow(
                    initial_state,
                    repo_root=repo_root,
                    implementation_hop_agents=implementation_hop_agents,
                    impl_agent=impl_agent,
                    scenario_coverage_agent=scenario_coverage_agent,
                    coverage_activation_agent=coverage_activation_agent,
                    apply_agent=apply_agent,
                    scenario_test_repair_agent=scenario_test_repair_agent,
                    group_error_agent=group_error_agent,
                    repair_order_agent=repair_order_agent,
                    diagnostic_agent=diagnostic_agent,
                    static_triage_agent=static_triage_agent,
                    typing_repair_agent=typing_repair_agent,
                    test_triage_agent=test_triage_agent,
                    test_repair_agent=test_repair_agent,
                    project_bug_hunt_agent=project_bug_hunt_agent,
                    bugbot_agent=bugbot_agent,
                    security_review_agent=security_review_agent,
                    archive_verify_agent=archive_verify_agent,
                    repair_adapter=apply_adapter,
                    thread_id="",
                    resume_scope=resume_scope,
                )
            except OpsxStageStop:
                raise
            except BaseException as exc:
                from agents.abc_agent import raise_cursor_infra_failure

                raise_cursor_infra_failure(
                    exc,
                    state={
                        **initial_state,
                        "stream_label": f"verify-impl-{change_id}",
                    },
                )
            impl_paths = persist_impl_verify_artifacts(
                final_state,
                change_id=change_id,
                repo_root=repo_root,
                stream_log=stream_log,
            )
            outcome = dict(final_state)
            outcome.update(impl_paths)
            scope = WorkflowResumeScope(kind="verify_impl", change_id=change_id)
            outcome["run_dir"] = str(default_run_dir(repo_root, scope))
            health = bug_journal.health()
            print(
                f"BUG_JOURNAL: events={health.events} spooled={health.spooled} dropped={health.dropped}",
                flush=True,
            )
            return outcome
        finally:
            BugJournalProjector(repo_root).rebuild()
            reset_current_journal(journal_token)


def main(*, repo_root: Path | None = None) -> None:
    """CLI verify PP; ``repo_root`` пробрасывается в ``run_workflow`` (см. ``run_opsx_main.py``).

    Флаг ``-r`` / ``--reset-sm`` удаляет устаревший ``checkpoints.sqlite`` (если есть)
    и обнуляет ``repair_attempts`` в ``row_walk_journal.json``; прогон не использует
    durable LangGraph checkpoint (см. ``durable_langgraph_checkpoint_enabled``).
    """
    parser = argparse.ArgumentParser(
        description="OPSX verify workflow PHASE_IMPL (ImplVerifyAgent)",
    )
    parser.add_argument("change_id", help="OpenSpec change id")
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Корень репозитория (по умолчанию — runtime parent или аргумент main)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=CURSOR_HOP_HARD_DEADLINE_SECONDS,
        help="Overall adapter hop budget (seconds); default 45m for impl_verify",
    )
    parser.add_argument(
        "-r",
        "--reset-sm",
        action="store_true",
        help=(
            "сбросить checkpoint verify_impl и repair_attempts row-walk журнала перед запуском"
        ),
    )
    args = parser.parse_args()
    resolved_root = resolve_opsx_repository_root(
        args.repo or repo_root or REPO_ROOT,
    )
    if args.reset_sm:
        reset_verify_impl_checkpoints(resolved_root, args.change_id)
        reset_row_walk_journal_repair_attempts(resolved_root, args.change_id)
    try:
        outcome = asyncio.run(
            execute(
                args.change_id,
                repo_root=resolved_root,
                timeout_seconds=args.timeout,
            ),
        )
    except OpsxStageStop as exc:
        print(format_stage_stop(exc), file=sys.stderr)
        sys.exit(1)
    rewind_note = outcome.get("verify_rewind_note")
    if isinstance(rewind_note, str) and rewind_note.strip():
        print(rewind_note, file=sys.stderr)
    raise_if_implementation_lane_terminal(outcome, change_id=args.change_id)
    raise_unless_security_pass(outcome, change_id=args.change_id)
    print(
        format_verify_impl_security_pass_cli_status(
            outcome,
            change_id=args.change_id,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
