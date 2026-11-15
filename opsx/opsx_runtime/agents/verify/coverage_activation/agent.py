"""CoverageActivationAgent: inventory, static/test activation перед quality gates."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    ABCAdapter,
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.state import GraphState
from agents.check_receipts import best_effort_write_pass, read_pass
from agents.verify.check_reuse import review_receipt_key
from agents.verify.coverage_activation.git_inventory import (
    collect_change_inventory,
    merge_prior_accumulated,
)
from agents.verify.coverage_activation.green_resume import (
    coverage_activation_complete_after_hop,
    coverage_activation_may_skip_green_resume,
    patch_clear_coverage_activation_apply_code_changed,
)
from agents.verify.coverage_activation.prompt.get_prompt import get_prompt
from agents.verify.coverage_activation.report import format_coverage_activation_report
from agents.verify.coverage_activation.run_checks import (
    checker_available,
    run_mypy_on_paths,
    run_pyright_on_paths,
    run_pytest_on_paths,
)
from agents.verify.coverage_activation.mypy_static import (
    audit_mypy_paths,
    build_mypy_activation_plan,
    register_mypy_paths,
)
from agents.verify.coverage_activation.schemas import CoverageActivationAnswer
from agents.verify.coverage_activation.static_coverage import (
    apply_static_activation_plan,
    audit_static_paths,
    build_static_activation_plan,
)
from agents.verify.coverage_activation.test_discovery import (
    apply_test_reactivation,
    build_test_reactivation_plan,
    discover_related_tests,
)

_READ_TOOLS = ("read", "grep", "glob", "ls")


def _metrics_from_audit(
    *,
    inventory: dict[str, Any],
    static_audit: dict[str, Any],
    related_count: int,
    disabled_related: int,
    static_activated: int,
    tests_reactivated: int,
    blocked: int,
    mypy_registered: int = 0,
) -> dict[str, int]:
    changed = len(inventory.get("current_changed") or []) + len(
        inventory.get("historical_touched") or [],
    )
    return {
        "changed_files": changed,
        "already_covered": int(static_audit.get("already_covered_count") or 0),
        "static_activated": static_activated,
        "related_tests": related_count,
        "disabled_related": disabled_related,
        "tests_reactivated": tests_reactivated,
        "blocked": blocked,
        "mypy_registered": mypy_registered,
    }


def _deterministic_pass_answer(
    *,
    change_id: str,
    workspace: str,
    summary: str,
    metrics: dict[str, int],
    test_gaps: list[str] | None = None,
) -> CoverageActivationAnswer:
    return CoverageActivationAnswer(
        verdict="COVERAGE_ACTIVATION_PASS",
        summary=summary,
        approve_test_reactivation=[],
        approve_static_paths=[],
        test_coverage_gaps=test_gaps or [],
        local_hop_copy_block=(
            f"Workspace: {workspace}\n"
            f"Change: {change_id}\n"
            f"Verdict: COVERAGE_ACTIVATION_PASS\n"
            f"Metrics: {metrics}"
        ),
    )


class CoverageActivationAgent:
    """Один hop: детерминированный inventory/audit + опциональный LLM + apply."""

    def __init__(
        self,
        adapter: ABCAdapter,
        receipt: Any | None = None,
    ) -> None:
        from check_workflow_receipt import CheckWorkflowReceipt

        self._adapter = adapter
        self._receipt = receipt if receipt is not None else CheckWorkflowReceipt.none()

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить coverage activation между scenario coverage и quality gates.

        Git-inventory и ``content_digest`` выполняются в ``asyncio.to_thread``, чтобы
        не блокировать event loop (общий Cursor bridge / ``ainvoke`` verify subgraph).
        """
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)
        repo_root = Path(repo)

        # ``opsx.verify.routing`` уже на stderr (см. ``ensure_workflow_stderr_logging``).
        logging.getLogger("opsx.verify.routing").info(
            "verify coverage_activation agent.run change_id=%s repository=%s",
            change_id,
            repo,
        )

        prior_fp = str(state.get("coverage_activation_fingerprint") or "")
        prior_complete = state.get("coverage_activation_complete") is True
        accumulated = merge_prior_accumulated(state)
        inventory_obj = await asyncio.to_thread(
            collect_change_inventory,
            repo_root,
            prior_accumulated=accumulated,
        )
        inventory = inventory_obj.as_dict()
        inventory["accumulated_touched"] = sorted(
            set(inventory.get("current_changed") or [])
            | set(inventory.get("historical_touched") or [])
            | set(accumulated),
        )
        fingerprint = inventory_obj.fingerprint()

        # Reuse ONLY a fully clean, non-mutating activation. Never replay an
        # activation that edited tests/static config, ran checks, or left gaps.
        key = review_receipt_key(
            state, role="coverage_activation",
            prompt=f"coverage_activation_inventory_v1:{fingerprint}",
        )
        if (key is not None and inventory_obj.status == "READY"
                and state.get("coverage_activation_apply_code_changed") is not True):
            cached = read_pass(
                repo_root, change_id=change_id, check="coverage_activation",
                contract="coverage_activation_noop_v1",
                input_digest=key.digest,
                verifier_contract_digest=key.verifier_contract_digest,
                pass_verdict="COVERAGE_ACTIVATION_PASS",
            )
            if cached is not None:
                raw = cached.get("result") or {}
                try:
                    cached_answer = CoverageActivationAnswer.model_validate(raw["answer"])
                    cached_metrics = dict(raw["metrics"])
                    if (
                        cached_answer.verdict == "COVERAGE_ACTIVATION_PASS"
                        and not cached_answer.test_coverage_gaps
                        and all(
                            int(cached_metrics.get(name, -1)) == 0
                            for name in ("static_activated", "tests_reactivated",
                                         "mypy_registered", "blocked")
                        )
                        and not raw.get("checks")
                    ):
                        patch = self._finalize(
                            state, change_id, inventory, cached_answer,
                            metrics=cached_metrics, checks={},
                            fingerprint=fingerprint, skipped=True,
                            prior_complete=True,
                        )
                        patch["coverage_activation_execution_status"] = "REUSED_PASS"
                        patch["coverage_activation_receipt"] = {
                            "source": "durable_pass",
                            "current_execution_id": key.execution_id,
                            "origin_execution_id": cached["origin_execution_id"],
                            "evidence_id": cached["evidence_id"],
                        }
                        return patch
                except (KeyError, ValueError, TypeError):
                    pass

        if inventory_obj.status == "BLOCKED":
            answer = CoverageActivationAnswer(
                verdict="COVERAGE_ACTIVATION_BLOCKED",
                summary="Inventory заблокирован: нет надёжного git base.",
                block_reason=inventory_obj.block_reason,
                local_hop_copy_block=(
                    f"Workspace: {workspace}\nChange: {change_id}\n"
                    f"Reason: {inventory_obj.block_reason}"
                ),
            )
            return self._finalize(state, change_id, inventory, answer, metrics={}, checks={})

        if inventory_obj.status == "NOT_APPLICABLE":
            metrics = _metrics_from_audit(
                inventory=inventory,
                static_audit={"already_covered_count": 0},
                related_count=0,
                disabled_related=0,
                static_activated=0,
                tests_reactivated=0,
                blocked=0,
            )
            answer = _deterministic_pass_answer(
                change_id=change_id,
                workspace=workspace,
                summary="Нет in-policy изменений — activation не требуется.",
                metrics=metrics,
            )
            return self._finalize(state, change_id, inventory, answer, metrics=metrics, checks={})

        if coverage_activation_may_skip_green_resume(
            state,
            prior_complete=prior_complete,
            prior_fingerprint=prior_fp,
            fingerprint=fingerprint,
        ):
            metrics_raw = state.get("coverage_activation_metrics")
            metrics = dict(metrics_raw) if isinstance(metrics_raw, dict) else {}
            answer_raw = state.get("coverage_activation_answer")
            if isinstance(answer_raw, dict):
                answer = CoverageActivationAnswer.model_validate(answer_raw)
                return self._finalize(
                    state,
                    change_id,
                    inventory,
                    answer,
                    metrics=metrics,
                    checks=state.get("coverage_activation_checks") or {},
                    skipped=True,
                    prior_complete=prior_complete,
                )

        all_paths = tuple(
            sorted(
                set(inventory.get("current_changed") or [])
                | set(inventory.get("historical_touched") or []),
            ),
        )
        caps = checker_available(repo_root)
        pyright_config_missing = not caps.get("pyright_config")

        static_audit = audit_static_paths(
            repo_root,
            all_paths,
            deleted_paths=tuple(inventory.get("deleted_paths") or []),
        )
        related = discover_related_tests(repo_root, all_paths)
        related_payload = [
            {
                "path": r.path,
                "relation": r.relation,
                "skip_kind": r.skip_kind,
                "skip_reason": r.skip_reason,
                "unsafe": r.unsafe,
            }
            for r in related
        ]

        needs_llm = any(
            row.get("disposition") in ("HIT_EXCLUDE", "EXCLUDE_OVERRIDES_INCLUDE")
            for row in (static_audit.get("rows") or [])
            if isinstance(row, dict)
        ) or any(r.skip_kind != "none" and not r.unsafe for r in related)

        static_plan = build_static_activation_plan(static_audit, allow_narrow_exclude=False)
        approved_tests: set[str] = set()
        approved_static: set[str] = set()
        test_gaps: list[str] = []
        for prod in all_paths:
            if not any(prod in r.relation for r in related):
                test_gaps.append(prod)

        session_patch: dict[str, Any]
        if needs_llm:
            prompt = get_prompt(
                repository_path=workspace,
                change_id=change_id,
                inventory=inventory,
                static_audit=static_audit,
                related_tests=related_payload,
            )
            outcome = await self._adapter.run(
                adapter_transport_state(
                    state,
                    stream_log=state.get("coverage_activation_stream_log"),
                    stream_label=compose_agent_stream_label(state, "coverage_activation"),
                ),
                prompt=prompt,
                agent_key="coverage_activation",
                tools=_READ_TOOLS,
                resume_agent_id=resume_for_role(state, "coverage_activation_agent_id"),
                payload_model=CoverageActivationAnswer,
                format_instructions=schema_format_reminder(CoverageActivationAnswer),
                max_validation_attempts=4,
            )
            answer = CoverageActivationAnswer.model_validate(
                outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
            )
            approved_tests = set(answer.approve_test_reactivation)
            approved_static = set(answer.approve_static_paths)
            test_gaps = list(answer.test_coverage_gaps)
            session_patch = agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="coverage_activation_agent_id",
            )
            session_patch.update(
                {
                    "coverage_activation_run_id": outcome.run_id,
                    "coverage_activation_execution_status": outcome.status,
                },
            )
        else:
            answer = _deterministic_pass_answer(
                change_id=change_id,
                workspace=workspace,
                summary="Детерминированная activation: только additive include/ignore.",
                metrics={},
            )
            approved_tests = {r.path for r in related if r.skip_kind != "none" and not r.unsafe}
            for path in static_plan.get("include_add") or []:
                approved_static.add(str(path))
            for path in static_plan.get("ignore_remove") or []:
                approved_static.add(str(path))
            session_patch = {
                "coverage_activation_agent_id": None,
                "coverage_activation_run_id": None,
                "coverage_activation_execution_status": "deterministic",
            }

        if static_plan.get("blocked") and not approved_static:
            for path in static_plan.get("include_add") or []:
                approved_static.add(str(path))
            for path in static_plan.get("ignore_remove") or []:
                approved_static.add(str(path))
            blocked_note = (
                f" (широкий exclude: {len(static_plan.get('blocked') or [])} путей — "
                "продолжаем static_repair)"
            )
            if answer.verdict == "COVERAGE_ACTIVATION_PASS":
                answer = answer.model_copy(
                    update={"summary": answer.summary + blocked_note},
                )
            elif answer.verdict == "COVERAGE_ACTIVATION_NEEDS_OWNER":
                answer = answer.model_copy(
                    update={
                        "verdict": "COVERAGE_ACTIVATION_PASS",
                        "block_reason": "",
                        "summary": (
                            str(answer.summary or "").strip()
                            + blocked_note
                        ).strip(),
                    },
                )

        apply_plan = {
            "include_add": list(static_plan.get("include_add") or []),
            "ignore_remove": list(static_plan.get("ignore_remove") or []),
            "exclude_narrow": [],
            "blocked": list(static_plan.get("blocked") or []),
        }
        for path in approved_static:
            if path not in apply_plan["include_add"] and path not in apply_plan["ignore_remove"]:
                apply_plan["include_add"].append(path)
        apply_plan["include_add"] = sorted(set(apply_plan["include_add"]))
        apply_plan["ignore_remove"] = sorted(set(apply_plan["ignore_remove"]))
        static_apply = apply_static_activation_plan(repo_root, apply_plan)
        static_activated = len(static_apply.get("include_add") or []) + len(
            static_apply.get("ignore_remove") or [],
        )

        test_plan = build_test_reactivation_plan(related, approved_paths=approved_tests)
        test_apply = apply_test_reactivation(repo_root, test_plan.get("reactivate") or [])
        tests_reactivated = len(test_apply.get("edited") or [])

        reactivated_tests = list(test_apply.get("edited") or [])
        if reactivated_tests:
            pyright_test_apply = apply_static_activation_plan(
                repo_root,
                {"include_add": reactivated_tests, "ignore_remove": []},
            )
            if pyright_test_apply.get("applied"):
                static_activated += len(reactivated_tests)

        static_check_paths = sorted(
            set(apply_plan.get("include_add") or [])
            | set(apply_plan.get("ignore_remove") or [])
            | set(reactivated_tests),
        )
        mypy_audit = audit_mypy_paths(repo_root, tuple(static_check_paths))
        mypy_plan = build_mypy_activation_plan(mypy_audit)
        mypy_register = register_mypy_paths(repo_root, mypy_plan.get("register") or [])
        mypy_registered = len(mypy_register.get("added") or [])

        checks: dict[str, Any] = {}
        if static_check_paths and caps.get("poetry"):
            if caps.get("pyright_config"):
                checks["pyright"] = run_pyright_on_paths(repo_root, static_check_paths)
            if caps.get("mypy_config"):
                checks["mypy"] = run_mypy_on_paths(repo_root, static_check_paths)
        if reactivated_tests and caps.get("poetry"):
            checks["pytest"] = await asyncio.to_thread(
                run_pytest_on_paths,
                repo_root,
                reactivated_tests,
            )

        blocked_count = len(static_plan.get("blocked") or []) + len(test_plan.get("blocked") or [])
        metrics = _metrics_from_audit(
            inventory=inventory,
            static_audit=static_audit,
            related_count=len(related),
            disabled_related=int(test_plan.get("disabled_related_count") or 0),
            static_activated=static_activated,
            tests_reactivated=tests_reactivated,
            blocked=blocked_count,
            mypy_registered=mypy_registered,
        )

        if answer.verdict == "COVERAGE_ACTIVATION_PASS" and test_gaps:
            answer = answer.model_copy(
                update={
                    "test_coverage_gaps": test_gaps,
                    "summary": answer.summary + " (есть test coverage gaps)",
                },
            )

        if static_apply.get("applied") or test_apply.get("edited") or mypy_registered:
            patch_workflow = {"verify_workflow_code_changed": True}
        else:
            patch_workflow = {}

        if pyright_config_missing and not static_apply.get("applied"):
            answer = CoverageActivationAnswer(
                verdict="COVERAGE_ACTIVATION_BLOCKED",
                summary=(
                    "Pyright strict config недоступен; static activation ограничена. "
                    "Pipeline продолжается в static_repair."
                ),
                block_reason="PYRIGHT_STRICT_CONFIG_MISSING",
                approve_test_reactivation=list(approved_tests),
                approve_static_paths=list(approved_static),
                test_coverage_gaps=test_gaps,
                local_hop_copy_block=(
                    f"Workspace: {workspace}\nChange: {change_id}\n"
                    f"Tests reactivated: {tests_reactivated}"
                ),
            )

        result = self._finalize(
            state,
            change_id,
            inventory,
            answer,
            metrics=metrics,
            checks=checks,
            fingerprint=fingerprint,
            prior_complete=prior_complete,
        )
        result.update(session_patch)
        result.update(patch_workflow)
        if not result.get("coverage_activation_skipped_resume"):
            result.update(patch_clear_coverage_activation_apply_code_changed())
        saved = False
        if (
            key is not None
            and answer.verdict == "COVERAGE_ACTIVATION_PASS"
            and not answer.test_coverage_gaps
            and not patch_workflow
            and not checks
            and all(
                int(metrics.get(name, -1)) == 0
                for name in ("static_activated", "tests_reactivated",
                             "mypy_registered", "blocked")
            )
        ):
            next_key = review_receipt_key(
                state, role="coverage_activation",
                prompt=f"coverage_activation_inventory_v1:{fingerprint}",
            )
            if key == next_key:
                saved = best_effort_write_pass(
                    root=repo_root, change_id=change_id, check="coverage_activation",
                    contract="coverage_activation_noop_v1",
                    input_digest=key.digest,
                    verifier_contract_digest=key.verifier_contract_digest,
                    pass_verdict="COVERAGE_ACTIVATION_PASS",
                    origin_execution_id=key.execution_id,
                    evidence_id=str(result.get("coverage_activation_run_id") or "deterministic-noop"),
                    result={
                        "verdict": "COVERAGE_ACTIVATION_PASS",
                        "answer": answer.model_dump(mode="json"),
                        "metrics": metrics, "checks": {},
                    },
                )
        result["coverage_activation_receipt"] = {
            "source": "fresh_activation", "saved": saved,
            "current_execution_id": str(state.get("run_uuid") or ""),
        }
        return result

    def _finalize(
        self,
        state: GraphState,
        change_id: str,
        inventory: dict[str, Any],
        answer: CoverageActivationAnswer,
        *,
        metrics: dict[str, Any],
        checks: dict[str, Any],
        fingerprint: str = "",
        skipped: bool = False,
        prior_complete: bool = False,
    ) -> dict[str, Any]:
        markers = {
            "COVERAGE_ACTIVATION_PASS": "✅ COVERAGE_ACTIVATION_PASS",
            "COVERAGE_ACTIVATION_BLOCKED": "⏸ COVERAGE_ACTIVATION_BLOCKED",
            "COVERAGE_ACTIVATION_NEEDS_OWNER": "⚠ COVERAGE_ACTIVATION_OWNER",
        }
        marker = markers.get(answer.verdict, "⏸ COVERAGE_ACTIVATION_UNKNOWN")
        report_text = format_coverage_activation_report(
            change_id=change_id,
            answer=answer,
            marker=marker,
            metrics=metrics,
            inventory=inventory,
        )
        complete = coverage_activation_complete_after_hop(
            verdict=answer.verdict,
            skipped=skipped,
            prior_complete=prior_complete,
        )
        return {
            "coverage_activation_verdict": answer.verdict,
            "coverage_activation_marker": marker,
            "coverage_activation_report_text": report_text,
            "coverage_activation_answer": answer.model_dump(mode="json"),
            "coverage_activation_inventory": inventory,
            "coverage_activation_metrics": metrics,
            "coverage_activation_checks": checks,
            "coverage_activation_fingerprint": fingerprint or inventory.get("base_sha", ""),
            "coverage_activation_complete": complete,
            "coverage_activation_accumulated_touched": inventory.get("accumulated_touched") or [],
            "coverage_activation_skipped_resume": skipped,
        }
