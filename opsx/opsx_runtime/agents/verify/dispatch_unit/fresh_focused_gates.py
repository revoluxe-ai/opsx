"""Свежие focused прогоны quality gates для post-repair verify группы findings.

Не использует pre-repair ``quality_gate_diagnostics`` как доказательство RESOLVED.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents.verify.group_error.schemas import Finding
from agents.verify.dispatch_unit.repro_cohort import (
    pytest_node_id, pytest_module, safe_pytest_modules,
)
from workflows.repo_paths import resolve_opsx_repository_root

GateTool = Literal["pytest", "pyright", "mypy"]


@dataclass(frozen=True)
class FreshGateRun:
    """Результат одного focused subprocess."""

    tool: GateTool
    ok: bool
    issues: list[str]
    detail: str
    exit_code: int | None = None


def _infer_tool(finding: Finding) -> GateTool:
    tool = finding.source_tool
    if tool == "pytest":
        return "pytest"
    if tool == "mypy":
        return "mypy"
    wf = str(finding.repair_workflow or "")
    if wf.startswith("WF_MYPY_"):
        return "mypy"
    if wf.startswith("WF_PYTEST_"):
        return "pytest"
    return "pyright"


def _paths_for_findings(findings: list[Finding]) -> set[str]:
    from agents.verify.dispatcher.items import parse_path_line

    paths: set[str] = set()
    for finding in findings:
        observation = str(finding.observation or "")
        node = pytest_node_id(observation)
        path, _ = parse_path_line(observation)
        if node:
            paths.add(pytest_module(node))
        elif path:
            paths.add(path)
    return paths


def run_fresh_gate_for_tool(
    repo_root: Path,
    tool: GateTool,
    *,
    rel_paths: set[str],
    pytest_modules: list[str] | None = None,
    full_suite: bool = False,
) -> FreshGateRun:
    """Запустить один gate tool; ``ok=False`` если subprocess не удался."""
    from workflows.verify.gate_repair.diagnostics import parse_pyright_issues
    from workflows.verify.gate_repair.mypy_output import parse_mypy_issues
    from workflows.verify.gate_repair.runner import (
        DEFAULT_GATE_TIMEOUT_SECONDS,
        _run_gate_subprocess,
        build_quality_gate_commands,
    )
    from workflows.verify.pytest_execute import PytestExecuteRequest, pytest_execute_state_patch
    from workflows.verify.pytest_execute.invoke import run_pytest_execute_sync
    from workflows.verify.gate_repair.diagnostics import parse_pytest_issues

    commands = build_quality_gate_commands(repo_root)
    base = list(commands[tool])
    if tool == "pytest" and not full_suite:
        from agents.repair.scenario_test_repair.workflow_pytest_defer import (
            workflow_skip_pytest_module,
        )

        modules = safe_pytest_modules(
            repo_root, pytest_modules if pytest_modules is not None else sorted(rel_paths)
        )
        rel_for_scope = [
            str(Path(m).relative_to(repo_root)) if Path(m).is_absolute() else m
            for m in modules
            if not workflow_skip_pytest_module(
                repo_root,
                str(Path(m).relative_to(repo_root)) if Path(m).is_absolute() else m,
            )
        ]
        if not rel_for_scope:
            return FreshGateRun(
                tool="pytest",
                ok=False,
                issues=[],
                detail="WORKFLOW_PYTEST_DEFERRED_SKIP",
                exit_code=None,
            )
        execute_result = run_pytest_execute_sync(
            repo_root,
            PytestExecuteRequest(
                scope_kind="COHORT",
                module_paths=rel_for_scope,
                max_harness_attempts=0,
            ),
        )
        gate = execute_result.gate_check
        if gate is None:
            return FreshGateRun(
                tool="pytest",
                ok=False,
                issues=[],
                detail=f"pytest_execute produced no gate: {execute_result.detail}",
            )
        full_log = f"{gate.stdout_tail}\n{gate.stderr_tail}"
        issues = _parse_pytest_failures(full_log) or parse_pytest_issues(full_log)
        subprocess_ok = (
            gate.error is None
            and gate.exit_code in (0, 1)
            and (gate.exit_code == 0 or bool(issues))
        )
        _ = pytest_execute_state_patch(execute_result)
        return FreshGateRun(
            tool=tool,
            ok=subprocess_ok,
            issues=issues,
            detail=f"pytest_execute exit={gate.exit_code} issues={len(issues)}",
            exit_code=gate.exit_code,
        )
    if tool == "pytest" and full_suite:
        execute_result = run_pytest_execute_sync(
            repo_root,
            PytestExecuteRequest(scope_kind="FULL_SUITE", module_paths=[]),
        )
        gate = execute_result.gate_check
        if gate is None:
            return FreshGateRun(tool="pytest", ok=False, issues=[], detail=execute_result.detail)
        full_log = f"{gate.stdout_tail}\n{gate.stderr_tail}"
        issues = _parse_pytest_failures(full_log) or parse_pytest_issues(full_log)
        subprocess_ok = gate.error is None and gate.exit_code in (0, 1)
        return FreshGateRun(
            tool=tool,
            ok=subprocess_ok,
            issues=issues,
            detail=execute_result.detail,
            exit_code=gate.exit_code,
        )
    elif tool == "pyright" and rel_paths:
        cmd = base + sorted(rel_paths)
    elif tool == "mypy" and rel_paths:
        cmd = base[:]
        for p in sorted(rel_paths):
            if p not in cmd:
                cmd.append(p)
    else:
        cmd = base
    _result, full_log = _run_gate_subprocess(
        tool,
        cmd,
        cwd=repo_root,
        timeout_seconds=DEFAULT_GATE_TIMEOUT_SECONDS,
    )
    if tool == "pyright":
        issues = parse_pyright_issues(full_log)
    elif tool == "mypy":
        issues = parse_mypy_issues(full_log)
    else:
        issues = _parse_pytest_failures(full_log)
    subprocess_ok = (
        _result.error is None
        and _result.exit_code in (0, 1)
        and (_result.exit_code == 0 or bool(issues))
    )
    return FreshGateRun(
        tool=tool,
        ok=subprocess_ok,
        issues=issues,
        detail=f"exit={_result.exit_code} issues={len(issues)} argv={cmd!r}",
        exit_code=_result.exit_code,
    )


def _parse_pytest_failures(text: str) -> list[str]:
    issues: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED ") or stripped.startswith("ERROR "):
            issues.append(stripped)
    return issues


def observation_matches_fresh_issue(finding: Finding, fresh_issues: list[str]) -> bool | None:
    """``True`` если observation всё ещё видна в свежем выводе; ``None`` если нельзя сопоставить."""
    obs = (finding.observation or "").strip()
    if not obs:
        return None
    original_node = pytest_node_id(obs)
    if original_node:
        return any(pytest_node_id(line) == original_node for line in fresh_issues)
    for line in fresh_issues:
        text = line.strip()
        if not text:
            continue
        if obs == text or obs in text or text in obs:
            return True
    from agents.verify.dispatcher.items import parse_path_line

    path, line_no = parse_path_line(obs)
    if path and line_no is not None:
        needle = f"{path}:{line_no}"
        for line in fresh_issues:
            if needle in line:
                return True
        return False
    if not fresh_issues:
        return False
    return None


def collect_fresh_runs(
    repo_root: Path,
    findings: list[Finding],
    *,
    extra_paths: set[str],
    pytest_modules: list[str] | None = None,
) -> dict[GateTool, FreshGateRun | None]:
    """По одному fresh run на tool, нужный findings группы."""
    by_tool: dict[GateTool, list[Finding]] = {"pytest": [], "pyright": [], "mypy": []}
    for f in findings:
        by_tool[_infer_tool(f)].append(f)
    paths_all = _paths_for_findings(findings) | extra_paths
    out: dict[GateTool, FreshGateRun | None] = {}
    for tool in ("pytest", "pyright", "mypy"):
        if not by_tool[tool]:
            continue
        tool_paths = _paths_for_findings(by_tool[tool]) | (
            paths_all if tool in ("pyright", "mypy") else set()
        )
        try:
            out[tool] = run_fresh_gate_for_tool(
                repo_root, tool, rel_paths=tool_paths,
                pytest_modules=pytest_modules if tool == "pytest" else None,
            )
        except (OSError, ValueError) as exc:
            out[tool] = FreshGateRun(
                tool=tool, ok=False, issues=[],
                detail=f"COHORT_GATE_UNAVAILABLE: {type(exc).__name__}: {exc}",
            )
    return out


def resolve_repo_root(state_repository: str) -> Path | None:
    """OPSX root или ``None`` если окружение не позволяет fresh gates."""
    try:
        return resolve_opsx_repository_root(Path(state_repository))
    except ValueError:
        return None
