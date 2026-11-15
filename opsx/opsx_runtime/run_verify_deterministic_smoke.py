"""Smoke: deterministic quality gates → results analyst → static repair subgraph.

Gates: ``poetry run`` subprocess (project venv). LLM hops: LangGraph runtime venv
(``runtime_bootstrap``, тот же runtime что repair).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.provider_registry import build_review_adapter  # noqa: E402
from runtime_bootstrap import ensure_provider_wheel_importable  # noqa: E402

from agents.state import GraphState  # noqa: E402
from agents.verify.group_error.agent import GroupErrorAgent  # noqa: E402
from agents.verify.repair_order.agent import RepairOrderAgent  # noqa: E402
from agents.verify.diagnostic.agent import DiagnosticAgent  # noqa: E402
from agents.verify.static_triage.agent import StaticTriageAgent  # noqa: E402
from agents.verify.test_repair.agent import TestRepairAgent  # noqa: E402
from agents.verify.test_triage.agent import TestTriageAgent  # noqa: E402
from agents.verify.typing_repair.agent import TypingRepairAgent  # noqa: E402
from workflows.verify.gate_repair.diagnostics import extract_gate_diagnostics  # noqa: E402
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle  # noqa: E402
from workflows.verify.gate_repair.subgraph import build_gate_repair_subgraph  # noqa: E402
from workflows.verify.tests.stub_adapters import (  # noqa: E402
    StubGroupErrorAdapter,
    StubRepairOrderAdapter,
    StubDiagnosticAdapter,
    StubStaticTriageAdapter,
    StubTypingRepairAdapter,
    StubTestTriageAdapter,
    StubTestRepairAdapter,
)

_CONFIGS_ROOT = resource_path(REPO_ROOT, "opsx", "configs")
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))


def _model_selection(model: Any) -> dict[str, Any]:
    from model_config.model_presets import super_low_model
    from model_config.models.base_model_setting import BaseModelSetting
    from model_config.resolve_model import ModelResolveResult
    from agents.adapters.hop_model_selection import model_selection_for_hop

    preset: BaseModelSetting = model if model is not None else super_low_model
    return model_selection_for_hop(ModelResolveResult(model=preset))


def _fake_bundle() -> QualityGateRawBundle:
    stub = GateCheckResult(
        check_id="pytest",
        command=["stub"],
        exit_code=0,
        duration_ms=0,
    )
    fail_pyright = stub.model_copy(
        update={
            "check_id": "pyright",
            "exit_code": 1,
            "stdout_tail": "src/foo.py:1:5 - error: stub",
        },
    )
    mypy_ok = stub.model_copy(
        update={
            "check_id": "mypy",
            "exit_code": 0,
            "stdout_tail": "Success: no issues found in 1 source file",
        },
    )
    return QualityGateRawBundle(pytest=stub, pyright=fail_pyright, mypy=mypy_ok)


def _summary_payload(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("quality_gate_raw") or {}
    diagnostics = result.get("quality_gate_diagnostics") or {}
    analysis = result.get("analysis_result") or {}
    repair_order = result.get("repair_order_result") or {}
    diagnostic = result.get("diagnostic_result") or {}
    summary = analysis.get("triage_summary") or {}
    return {
        "smoke": "verify-deterministic-subgraph",
        "change_id": result.get("change_id"),
        "quality_gate_verdict": result.get("quality_gate_verdict"),
        "quality_gate_marker": result.get("quality_gate_marker"),
        "quality_gates_execution_status": result.get("quality_gates_execution_status"),
        "pytest_exit": (raw.get("pytest") or {}).get("exit_code"),
        "pyright_exit": (raw.get("pyright") or {}).get("exit_code"),
        "mypy_exit": (raw.get("mypy") or {}).get("exit_code"),
        "diagnostics_total_count": diagnostics.get("total_count"),
        "group_error_run_id": result.get("group_error_run_id"),
        "analysis_findings_total": summary.get("findings_total"),
        "repair_order_status": repair_order.get("status"),
        "repair_order_units_total": len(repair_order.get("ordered_units") or []),
        "repair_order_run_id": result.get("repair_order_run_id"),
        "diagnostic_status": diagnostic.get("status"),
        "diagnostic_anchor_unit_id": diagnostic.get("anchor_unit_id"),
        "diagnostic_remaining_count": len(diagnostic.get("remaining_queue") or []),
        "diagnostic_run_id": result.get("diagnostic_run_id"),
        "dispatcher_cycle_status": result.get("dispatcher_cycle_status"),
        "dispatcher_outcomes_count": len(result.get("dispatcher_outcomes") or []),
        "static_triage_classification": (result.get("static_triage_result") or {}).get(
            "classification",
        ),
        "static_repair_status": result.get("static_repair_status"),
        "static_repair_issue_count": result.get("static_repair_issue_count"),
    }


def _full_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **_summary_payload(result),
        "quality_gate_raw": result.get("quality_gate_raw"),
        "quality_gate_diagnostics": result.get("quality_gate_diagnostics"),
        "quality_gate_report_text": result.get("quality_gate_report_text"),
        "analysis_result": result.get("analysis_result"),
        "repair_order_result": result.get("repair_order_result"),
        "diagnostic_result": result.get("diagnostic_result"),
        "dispatcher_outcomes": result.get("dispatcher_outcomes"),
        "static_triage_result": result.get("static_triage_result"),
        "static_repair_queue": result.get("static_repair_queue"),
        "static_repair_report_text": result.get("static_repair_report_text"),
    }


def _live_review_adapter(*, timeout_seconds: float):
    ensure_provider_wheel_importable()
    ensure_adapter_factories_registered()
    return build_review_adapter(timeout_seconds=timeout_seconds, allow_git_shell=False)


def _build_triage(*, stub_analyst: bool, timeout_seconds: float) -> GroupErrorAgent:
    if stub_analyst:
        return GroupErrorAgent(StubGroupErrorAdapter())
    return GroupErrorAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_repair_order(*, stub_analyst: bool, timeout_seconds: float) -> RepairOrderAgent:
    if stub_analyst:
        return RepairOrderAgent(StubRepairOrderAdapter())
    return RepairOrderAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_diagnostic(*, stub_analyst: bool, timeout_seconds: float) -> DiagnosticAgent:
    if stub_analyst:
        return DiagnosticAgent(StubDiagnosticAdapter())
    return DiagnosticAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_static_triage(*, stub_analyst: bool, timeout_seconds: float) -> StaticTriageAgent:
    if stub_analyst:
        return StaticTriageAgent(StubStaticTriageAdapter())
    return StaticTriageAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_typing_repair(*, stub_analyst: bool, timeout_seconds: float) -> TypingRepairAgent:
    if stub_analyst:
        return TypingRepairAgent(StubTypingRepairAdapter())
    return TypingRepairAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_test_triage(*, stub_analyst: bool, timeout_seconds: float) -> TestTriageAgent:
    if stub_analyst:
        return TestTriageAgent(StubTestTriageAdapter())
    return TestTriageAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


def _build_test_repair(*, stub_analyst: bool, timeout_seconds: float) -> TestRepairAgent:
    if stub_analyst:
        return TestRepairAgent(StubTestRepairAdapter())
    return TestRepairAgent(_live_review_adapter(timeout_seconds=timeout_seconds))


async def _run_subgraph(
    repo_root: Path,
    *,
    change_id: str,
    stub_gates: bool,
    stub_analyst: bool,
    stub_verify: bool,
    gate_timeout_seconds: float,
    analyst_timeout_seconds: float,
) -> dict[str, Any]:
    _ = gate_timeout_seconds

    async def _noop_quality_gates(_state: GraphState) -> dict[str, Any]:
        """При ``--stub-gates`` не перезапускать poetry pytest/pyright/mypy."""
        return {}

    async def _build_and_run() -> dict[str, Any]:
        graph = build_gate_repair_subgraph(
            _build_triage(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_repair_order(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_diagnostic(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_static_triage(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_typing_repair(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_test_triage(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            _build_test_repair(
                stub_analyst=stub_analyst,
                timeout_seconds=analyst_timeout_seconds,
            ),
            repair_adapter=None,
        )
        state: GraphState = {
            "repository_path": str(repo_root),
            "workspace": str(repo_root),
            "change_id": change_id,
            "verdict": "IMPL_OK",
        }
        if stub_gates:
            bundle = _fake_bundle()
            diagnostics = extract_gate_diagnostics(
                bundle,
                mypy_parse_log="Success: no issues found in 1 source file",
            )
            state["quality_gate_verdict"] = "FAIL"
            state["quality_gate_raw"] = bundle.model_dump(mode="json")
            state["quality_gate_diagnostics"] = diagnostics.as_handoff_dict()
            state["quality_gates_execution_status"] = "DETERMINISTIC"
        if not stub_analyst:
            state["model"] = _model_selection(None)
        return dict(await graph.ainvoke(state))

    patches: list[Any] = []
    if stub_gates:
        from unittest.mock import patch

        patches.append(
            patch(
                "workflows.verify.gate_repair.subgraph.run_quality_gates_node",
                new=_noop_quality_gates,
            ),
        )
    if stub_verify:
        from unittest.mock import patch

        from agents.verify.dispatch_unit.fresh_focused_gates import FreshGateRun

        def _stub_collect_fresh_runs(_repo_root, findings, *, extra_paths):
            _ = extra_paths
            from agents.verify.dispatch_unit.fresh_focused_gates import _infer_tool

            out: dict[str, FreshGateRun] = {}
            for finding in findings:
                tool = _infer_tool(finding)
                if tool not in out:
                    out[tool] = FreshGateRun(
                        tool=tool,
                        ok=True,
                        issues=[],
                        detail="stub-verify-smoke",
                    )
            return out

        patches.append(
            patch(
                "agents.verify.dispatch_unit.verify_group.collect_fresh_runs",
                side_effect=_stub_collect_fresh_runs,
            ),
        )

    if patches:
        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            out = await _build_and_run()
    else:
        out = await _build_and_run()
    out["change_id"] = change_id
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke gate-repair subgraph "
            "(group_error → repair_order → diagnostic → dispatch_cycle)"
        ),
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--change-id",
        default=None,
        help="OpenSpec change id (default: smoke-verify-<uuid>)",
    )
    parser.add_argument(
        "--stub-gates",
        action="store_true",
        help="Pre-seed FAIL gate diagnostics in state (no poetry subprocess)",
    )
    parser.add_argument(
        "--stub-analyst",
        action="store_true",
        help="Skip live adapter for group_error, repair_order, diagnostic, static_triage stubs",
    )
    parser.add_argument(
        "--stub-verify",
        action="store_true",
        help=(
            "Mock post-repair fresh gates (no poetry pyright/pytest/mypy); "
            "recommended with --stub-gates --stub-analyst"
        ),
    )
    parser.add_argument("--gate-timeout", type=float, default=600)
    parser.add_argument(
        "--analyst-timeout",
        type=float,
        default=1800,
        help="GroupErrorAgent hop timeout (s); 167+ findings often exceed 900s",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()
    repo = args.repo.resolve()
    change_id = args.change_id or f"smoke-verify-{uuid.uuid4().hex[:8]}"

    mode_parts = []
    if args.stub_gates:
        mode_parts.append("stub-gates")
    else:
        mode_parts.append("live-gates")
    mode_parts.append("stub-analyst" if args.stub_analyst else "live-analyst")
    if args.stub_verify:
        mode_parts.append("stub-verify")
    mode_label = ", ".join(mode_parts)

    result = asyncio.run(
        _run_subgraph(
            repo,
            change_id=change_id,
            stub_gates=args.stub_gates,
            stub_analyst=args.stub_analyst,
            stub_verify=args.stub_verify,
            gate_timeout_seconds=args.gate_timeout,
            analyst_timeout_seconds=args.analyst_timeout,
        ),
    )
    summary = _summary_payload(result)
    print(f"SMOKE verify deterministic subgraph ({mode_label})", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    if args.json_out is not None:
        full = _full_payload(result)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(full, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"WROTE {args.json_out}", flush=True)


if __name__ == "__main__":
    main()
