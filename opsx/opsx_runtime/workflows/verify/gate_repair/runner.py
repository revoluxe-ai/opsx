"""Детерминированный запуск pytest / pyright / mypy для gate-repair subgraph.

Проверяет product allowlist: ``tests/`` (pytest), ``src/stroy`` и product entry внутри
пакета (``pyrightconfig_strict.json``, ``mypy.ini``). Не включает ``opsx``, OPSX-launchers
и dev-tools.

Пишет ``quality_gate_raw``, ``quality_gate_diagnostics``, ``quality_gate_verdict`` и
``quality_gates_execution_status=DETERMINISTIC`` в GraphState — тот же контракт, что у
бывшего ``QualityGatesAgent`` (см. smoke ``run_verify_deterministic_smoke``).
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.verify.coverage_activation.mypy_static import load_registered_mypy_paths
from agents.verify.quality_gates_receipt import (
    QUALITY_VERIFIER_POLICY_FILES,
    QualityGateDurablePass,
    resolve_quality_gate_environment_id,
)
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.verify.gate_repair.diagnostics import (
    QualityGateDiagnostics,
    extract_gate_diagnostics,
    resolve_quality_gate_verdict,
)
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle
from workflows.verify.pytest_execute import PytestExecuteRequest, pytest_execute_state_patch, result_to_gate_check
from workflows.verify.gate_repair.pytest_round_progress import apply_pytest_round_progress
from workflows.verify.pytest_execute.invoke import run_pytest_execute, run_pytest_execute_sync

DEFAULT_GATE_TIMEOUT_SECONDS = 600.0
_LOG_TAIL_CHARS = 24_000
_STDIO_TAIL_CHARS = 3_500


def _tail(text: str, limit: int = _STDIO_TAIL_CHARS) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return f"… [truncated; showing last {limit} chars]\n{clean[-limit:]}"


def _resolve_poetry(repo_root: Path) -> list[str]:
    """Команда-префикс poetry для subprocess в корне репозитория."""
    poetry = shutil.which("poetry")
    if poetry:
        return [poetry]
    return ["python", "-m", "poetry"]


# Явный allowlist product-проверок (см. pyproject testpaths, pyrightconfig*, mypy.ini).
_PRODUCT_PYTEST_DIR = "tests"
_PRODUCT_MYPY_TARGETS = ("src/stroy",)
_QUALITY_PRODUCT_DIRS = ("src", "tests", "migrations", "configs")
_QUALITY_PRODUCT_CONFIGS = (
    "pyproject.toml", "poetry.lock", "mypy.ini", "pytest.ini",
    "pyrightconfig_strict.json", "pyrightconfig.json", "setup.cfg",
)
_QUALITY_VERIFIER_POLICY_FILES = QUALITY_VERIFIER_POLICY_FILES


def build_quality_gate_commands(repo_root: Path) -> dict[str, list[str]]:
    """Собрать argv для pytest, pyright и mypy (только ``src/``, ``tests/``, entry points)."""
    from workflows.verify.pytest_workflow_argv import (
        build_workflow_pytest_argv,
        default_workflow_pytest_paths,
    )

    repo_root = repo_root.resolve()
    pyright_project = resource_path(repo_root, "pyrightconfig_strict.json")
    mypy_args = [str(resource_path(repo_root, rel)) for rel in _PRODUCT_MYPY_TARGETS]
    for rel in load_registered_mypy_paths(repo_root):
        full = resource_path(repo_root, rel)
        if full.is_file():
            mypy_args.append(str(full))
    prefix = _resolve_poetry(repo_root)
    pytest_argv = build_workflow_pytest_argv(
        repo_root,
        paths=default_workflow_pytest_paths(repo_root),
        poetry_prefix=prefix,
    )
    commands: dict[str, list[str]] = {
        "pytest": pytest_argv,
        "pyright": [
            *prefix,
            "run",
            "python",
            "-m",
            "pyright",
            "--project",
            str(pyright_project),
        ],
        "mypy": [*prefix, "run", "python", "-m", "mypy", *mypy_args],
    }
    return commands


def _combine_stdio(stdout: str, stderr: str) -> str:
    """Объединить stdout/stderr для парсинга (без сохранения в state)."""
    parts = [stdout.strip(), stderr.strip()]
    return "\n".join(part for part in parts if part)


def _run_gate_subprocess(
    check_id: str,
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> tuple[GateCheckResult, str]:
    """Выполнить gate-команду: tails в result, полный log text для парсинга."""
    started = time.monotonic()
    try:
        proc = run_subprocess_utf8(
            command,
            cwd=cwd,
            timeout=timeout_seconds,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        full_log = _combine_stdio(proc.stdout or "", proc.stderr or "")
        return (
            GateCheckResult(
                check_id=check_id,  # type: ignore[arg-type]
                command=command,
                exit_code=int(proc.returncode),
                duration_ms=duration_ms,
                stdout_tail=_tail(proc.stdout or ""),
                stderr_tail=_tail(proc.stderr or ""),
                error=None,
            ),
            full_log,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return (
            GateCheckResult(
                check_id=check_id,  # type: ignore[arg-type]
                command=command,
                exit_code=124,
                duration_ms=duration_ms,
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr + "\nTIMEOUT"),
                error="timeout",
            ),
            _combine_stdio(stdout, stderr + "\nTIMEOUT"),
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return (
            GateCheckResult(
                check_id=check_id,  # type: ignore[arg-type]
                command=command,
                exit_code=127,
                duration_ms=duration_ms,
                stdout_tail="",
                stderr_tail=str(exc),
                error=str(exc),
            ),
            str(exc),
        )


def _quality_gate_cache_key(
    state: GraphState,
    *,
    root: Path,
    commands: dict[str, list[str]],
    timeout_seconds: float | None,
) -> tuple[str, str] | None:
    """Делегат в ``QualityGateDurablePass`` (product + verifier + local runtime)."""
    return QualityGateDurablePass.cache_key(
        state, root=root, commands=commands, timeout_seconds=timeout_seconds,
    )


def _gates_are_strict_pass(raw: dict[str, Any]) -> bool:
    if raw.get("quality_gate_verdict") != "PASS":
        return False
    diag = raw.get("quality_gate_diagnostics")
    if (not isinstance(diag, dict) or diag.get("total_count") != 0
            or diag.get("input_integrity_status") != "COMPLETE"):
        return False
    bundle = raw.get("quality_gate_raw")
    if not isinstance(bundle, dict):
        return False
    return all(
        isinstance(bundle.get(name), dict)
        and bundle[name].get("exit_code") == 0
        and not bundle[name].get("error")
        for name in ("pytest", "pyright", "mypy")
    )


def _run_pyright_mypy_gates(
    repo_root: Path,
    commands: dict[str, list[str]],
    timeout_seconds: float,
) -> tuple[GateCheckResult, GateCheckResult, str]:
    """Subprocess pyright + mypy (sync, для ``asyncio.to_thread``)."""
    pyright, _pyright_log = _run_gate_subprocess(
        "pyright",
        commands["pyright"],
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
    )
    mypy, mypy_full_log = _run_gate_subprocess(
        "mypy",
        commands["mypy"],
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
    )
    return pyright, mypy, mypy_full_log


def _assemble_quality_gates_patch(
    state: GraphState,
    *,
    repo_root: Path,
    change_id: str,
    commands: dict[str, list[str]],
    cache_key: tuple[str, str] | None,
    timeout_seconds: float,
    pytest_result: Any,
    pytest: GateCheckResult,
    pyright: GateCheckResult,
    mypy: GateCheckResult,
    mypy_full_log: str,
) -> dict[str, Any]:
    """Собрать patch GraphState из результатов subprocess."""
    _pytest_log = pytest_result.run_log or pytest.stdout_tail
    bundle = QualityGateRawBundle(pytest=pytest, pyright=pyright, mypy=mypy)
    diagnostics = extract_gate_diagnostics(
        bundle, mypy_parse_log=mypy_full_log, pytest_parse_log=_pytest_log,
    )
    verdict = resolve_quality_gate_verdict(pytest=pytest, diagnostics=diagnostics)
    marker = "✅ QUALITY_PASS" if verdict == "PASS" else "❌ QUALITY_FAIL"
    report = format_quality_gate_report(
        change_id=change_id,
        bundle=bundle,
        diagnostics=diagnostics,
        verdict=verdict,
        marker=marker,
    )
    patch: dict[str, Any] = {
        "quality_gate_raw": bundle.model_dump(mode="json"),
        "quality_gate_diagnostics": diagnostics.as_handoff_dict(),
        "quality_gate_verdict": verdict,
        "quality_gate_marker": marker,
        "quality_gate_report_text": report,
        "quality_gates_execution_status": "DETERMINISTIC",
        **pytest_execute_state_patch(pytest_result),
    }
    saved = QualityGateDurablePass.save_pass_if_eligible(
        state,
        root=repo_root,
        change_id=change_id,
        commands=commands,
        timeout_seconds=timeout_seconds,
        patch=patch,
        cache_key=cache_key,
        gates_are_strict_pass=_gates_are_strict_pass,
    )
    patch["quality_gate_receipt"] = {
        "source": "fresh_checks",
        "saved": saved,
        "current_execution_id": str(state.get("run_uuid") or ""),
        "environment_id": resolve_quality_gate_environment_id(state),
    }
    patch.update(
        apply_pytest_round_progress(state, patch.get("quality_gate_diagnostics")),
    )
    return patch


def _quality_gates_cache_hit(
    state: GraphState,
    *,
    repo_root: Path,
    change_id: str,
    commands: dict[str, list[str]],
    timeout_seconds: float | None,
) -> dict[str, Any] | None:
    """Вернуть durable PASS patch или None."""
    return QualityGateDurablePass.read_cached_pass(
        state,
        root=repo_root,
        change_id=change_id,
        commands=commands,
        timeout_seconds=timeout_seconds,
        gates_are_strict_pass=_gates_are_strict_pass,
    )


async def run_deterministic_quality_gates_async(
    state: GraphState,
    *,
    timeout_seconds: float | None = None,
    harness_repair_agent: Any | None = None,
    repair_adapter: Any | None = None,
) -> dict[str, Any]:
    """Async gates: pytest harness в event loop; pyright/mypy в thread pool."""
    raw_root = state.get("repository_path") or state.get("workspace")
    if not raw_root:
        raise ValueError("repository_path required for quality gates")
    repo_root = resolve_opsx_repository_root(Path(str(raw_root)))
    change_id = str(state.get("change_id") or "unknown")
    commands = build_quality_gate_commands(repo_root)
    force_fresh = bool(state.get("verify_force_fresh_quality_gates"))
    cached = None
    if not force_fresh:
        cached = _quality_gates_cache_hit(
            state,
            repo_root=repo_root,
            change_id=change_id,
            commands=commands,
            timeout_seconds=timeout_seconds,
        )
    if cached is not None:
        cached = {
            **cached,
            **apply_pytest_round_progress(
                state, cached.get("quality_gate_diagnostics"),
            ),
        }
        return cached
    cache_key: tuple[str, str] | None = None
    try:
        cache_key = _quality_gate_cache_key(
            state, root=repo_root, commands=commands,
            timeout_seconds=timeout_seconds,
        )
    except (ValueError, TypeError, OSError):
        cache_key = None
    pytest_request = PytestExecuteRequest(
        scope_kind="FULL_SUITE",
        module_paths=[],
        max_harness_attempts=3,
        timeout_seconds=timeout_seconds,
    )
    pytest_result = await run_pytest_execute(
        repo_root,
        pytest_request,
        state=state,
        harness_repair_agent=harness_repair_agent,
        repair_adapter=repair_adapter,
    )
    pytest = (
        pytest_result.gate_check
        if pytest_result.gate_check is not None
        else result_to_gate_check(pytest_result)
    )
    static_gate_timeout = (
        timeout_seconds
        if timeout_seconds is not None and timeout_seconds > 0
        else DEFAULT_GATE_TIMEOUT_SECONDS
    )
    pyright, mypy, mypy_full_log = await asyncio.to_thread(
        _run_pyright_mypy_gates,
        repo_root,
        commands,
        static_gate_timeout,
    )
    patch = _assemble_quality_gates_patch(
        state,
        repo_root=repo_root,
        change_id=change_id,
        commands=commands,
        cache_key=cache_key,
        timeout_seconds=timeout_seconds,
        pytest_result=pytest_result,
        pytest=pytest,
        pyright=pyright,
        mypy=mypy,
        mypy_full_log=mypy_full_log,
    )
    if force_fresh:
        patch["verify_force_fresh_quality_gates"] = False
    return patch


def run_deterministic_quality_gates_sync(
    state: GraphState,
    *,
    timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
    harness_repair_agent: Any | None = None,
    repair_adapter: Any | None = None,
) -> dict[str, Any]:
    """Sync wrapper для unit-тестов и CLI (``asyncio.run``)."""
    return asyncio.run(
        run_deterministic_quality_gates_async(
            state,
            timeout_seconds=timeout_seconds,
            harness_repair_agent=harness_repair_agent,
            repair_adapter=repair_adapter,
        ),
    )


def format_quality_gate_report(
    *,
    change_id: str,
    bundle: QualityGateRawBundle,
    diagnostics: QualityGateDiagnostics,
    verdict: str,
    marker: str,
) -> str:
    """Краткий markdown-отчёт по результатам gates (для Owner и артефактов)."""
    total = diagnostics.total_count
    lines = [
        f"# Quality gates — {change_id}",
        "",
        marker,
        "",
        "Deterministic run (no LLM).",
        "",
        "## Machine exits",
        f"- pytest: exit {bundle.pytest.exit_code} ({bundle.pytest.duration_ms} ms)",
        f"- pyright: exit {bundle.pyright.exit_code} ({bundle.pyright.duration_ms} ms)",
        f"- mypy: exit {bundle.mypy.exit_code} ({bundle.mypy.duration_ms} ms)",
        f"- parsed issue total: {total}",
        f"- mypy input integrity: {diagnostics.input_integrity_status}",
        "",
        "## Commands",
        f"- pytest: `{ ' '.join(bundle.pytest.command) }`",
        f"- pyright: `{ ' '.join(bundle.pyright.command) }`",
        f"- mypy: `{ ' '.join(bundle.mypy.command) }`",
        "",
    ]
    if verdict != "PASS":
        lines.append(f"Verdict: {verdict}")
    return "\n".join(lines)


async def run_quality_gates_node(state: GraphState) -> dict[str, Any]:
    """LangGraph-узел ``quality_gates``: subprocess gates без LLM."""
    raw_timeout = state.get("quality_gate_timeout_seconds")
    timeout: float | None = (
        float(raw_timeout)
        if isinstance(raw_timeout, (int, float)) and raw_timeout > 0
        else None
    )
    return await run_deterministic_quality_gates_async(
        state,
        timeout_seconds=timeout,
        harness_repair_agent=_quality_gates_harness_agent,
        repair_adapter=_quality_gates_repair_adapter,
    )


_quality_gates_harness_agent: Any | None = None
_quality_gates_repair_adapter: Any | None = None


def bind_quality_gates_agents(
    *,
    harness_repair_agent: Any | None,
    repair_adapter: Any | None,
) -> None:
    """Привязать harness/commit агенты к узлу ``quality_gates`` (вызывается из subgraph compile)."""
    global _quality_gates_harness_agent, _quality_gates_repair_adapter
    _quality_gates_harness_agent = harness_repair_agent
    _quality_gates_repair_adapter = repair_adapter
