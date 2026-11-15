"""Детерминированные collect-only прогоны и fast-path классификация pytest harness vs test.

Вызывается из workflow ``pytest_execute`` и из ``classify_repair_workflow``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DEFAULT_GATE_TIMEOUT_SECONDS = 600.0

RunHealthKind = Literal["BUILD", "TEST"]

_HARNESS_PATTERNS = (
    "syntaxerror",
    "indentationerror",
    "invalid syntax",
    "error collecting",
    "errors during collection",
    "importerror while loading conftest",
    "collection errors",
    "no tests collected",
    "hermeticboundaryerror",
)
_PYTEST_NODE_IN_OBS = re.compile(r"\.py::[\w]")


@dataclass(frozen=True)
class CollectHealthResult:
    """Результат ``pytest --collect-only`` на scope."""

    ok: bool
    exit_code: int
    command: list[str]
    log_tail: str
    duration_ms: int
    error: str | None = None


def scope_key_from_modules(modules: list[str]) -> str:
    """Нормализованный ключ scope для state cache."""
    normalized = sorted({m.replace("\\", "/").lstrip("./") for m in modules if m})
    return "|".join(normalized) if normalized else "empty"


def is_pytest_harness_observation(observation: str) -> bool:
    """Детерминированно: сборка/collection pytest vs FAILED node assertion."""
    obs = observation.strip()
    if not obs:
        return False
    lower = obs.lower()
    if "fixture '" in lower and "not found" in lower:
        return True
    if "ingressstorageerror" in lower or "test_database_url" in lower:
        return True
    if obs.startswith("pytest: FAILED ") or obs.startswith("FAILED "):
        node_part = obs.split("FAILED ", 1)[-1]
        if _PYTEST_NODE_IN_OBS.search(node_part):
            return False
    if obs.startswith("pytest: ERROR ") and _PYTEST_NODE_IN_OBS.search(obs):
        return False
    for pat in _HARNESS_PATTERNS:
        if pat in lower:
            return True
    if "pytest:" in lower and ".py::" not in obs:
        if any(x in lower for x in ("error", "collect", "syntax", "import")):
            return True
    path, _ = _parse_path(obs)
    if path and path.startswith("tests/") and ".py::" not in obs:
        if any(x in lower for x in ("error", "e   ", "cannot collect")):
            return True
    return False


def _parse_path(observation: str) -> tuple[str | None, int | None]:
    from agents.verify.dispatcher.items import parse_path_line

    return parse_path_line(observation)


def affected_test_paths(observation: str, *, extra: list[str] | None = None) -> list[str]:
    """Repo-relative ``tests/...py`` для collect-only scope."""
    paths: set[str] = set(extra or [])
    node_match = re.search(r"([\w./\\-]+\.py)(?:::|\s|$)", observation)
    if node_match:
        paths.add(node_match.group(1).replace("\\", "/"))
    path, _ = _parse_path(observation)
    if path and path.endswith(".py"):
        paths.add(path.replace("\\", "/"))
    if observation.strip().endswith(".py"):
        paths.add(observation.strip().replace("\\", "/"))
    out = sorted(p for p in paths if p.startswith("tests/") or "/tests/" in p)
    return out


def classify_pytest_observation_fast(observation: str) -> RunHealthKind | None:
    """HIGH confidence BUILD/TEST или ``None`` (нужен LLM в MVP-1)."""
    if is_pytest_harness_observation(observation):
        return "BUILD"
    if _PYTEST_NODE_IN_OBS.search(observation) or observation.startswith("pytest: FAILED "):
        return "TEST"
    return None


def is_harness_blocker(workflow: str | None) -> bool:
    return workflow == "WF_PYTEST_COLLECTION"


def build_collect_command(repo_root: Path, modules: list[str]) -> list[str]:
    """argv для ``pytest --collect-only`` (тот же tail, что у quality gates)."""
    from workflows.verify.gate_repair.runner import _resolve_poetry
    from workflows.verify.pytest_workflow_argv import build_workflow_pytest_argv

    prefix = _resolve_poetry(repo_root)
    paths = [str((resource_path(repo_root, m)).resolve() if not Path(m).is_absolute() else Path(m)) for m in modules]
    return build_workflow_pytest_argv(
        repo_root,
        paths=paths,
        poetry_prefix=prefix,
        collect_only=True,
    )


def run_collect_only(
    repo_root: Path,
    modules: list[str],
    *,
    timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
) -> CollectHealthResult:
    """Факт: собирается ли pytest scope."""
    if not modules:
        return CollectHealthResult(
            ok=False,
            exit_code=2,
            command=[],
            log_tail="empty module scope",
            duration_ms=0,
            error="empty_scope",
        )
    from workflows.verify.gate_repair.pytest_gate_timeout import (
        QUALITY_GATE_COLLECT_PLACEHOLDER_TEST_COUNT,
        QUALITY_GATE_PER_TEST_SECONDS,
    )
    from workflows.verify.gate_repair.pytest_subprocess_streaming import (
        run_pytest_gate_with_progress,
    )

    cmd = build_collect_command(repo_root, modules)
    scope_key = scope_key_from_modules(modules)
    started = time.monotonic()
    result, full_log = run_pytest_gate_with_progress(
        cmd,
        cwd=repo_root,
        timeout_seconds=timeout_seconds,
        scope=scope_key,
        test_count=QUALITY_GATE_COLLECT_PLACEHOLDER_TEST_COUNT,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    stall_or_timeout = result.error is not None
    return CollectHealthResult(
        ok=result.exit_code == 0 and not stall_or_timeout,
        exit_code=result.exit_code,
        command=cmd,
        log_tail=(full_log or result.stderr_tail or "")[-8000:],
        duration_ms=duration_ms,
        error=result.error if stall_or_timeout else None,
    )
