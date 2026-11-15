"""Детерминированное обнаружение harness/env когорт из gates (без LLM)."""

from __future__ import annotations

import os
import re
from typing import Any

from agents.state import GraphState
from agents.verify.environment_prep.schemas import (
    EnvironmentPrepCohort,
    EnvironmentPrepCohorts,
)
from agents.verify.group_error.gate_only_triage import findings_from_gate_state
from agents.verify.pytest_run_health import is_harness_blocker

MAX_ENVIRONMENT_PREP_ATTEMPTS = 2
_COLLECTION_FINDING_THRESHOLD = 3

_KIND_PRIORITY: dict[str, int] = {
    "hermetic_boundary": 1,
    "shared_fixtures": 2,
    "bootstrap_keys": 3,
    "pg_env": 4,
    "syntax_collection": 5,
    "generic_collection": 6,
}

_SHARED_FIXTURE_NAMES = frozenset(
    {
        "repo_root",
        "case",
        "session_factory",
        "db_session",
        "postgres_url",
        "test_database_url",
    },
)

_FIXTURE_NOT_FOUND = re.compile(r"fixture '([^']+)' not found", re.IGNORECASE)
_KEY_ERROR = re.compile(r"keyerror:\s*'([^']+)'", re.IGNORECASE)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def skip_environment_prep_enabled() -> bool:
    """Opt-out: ``OPSX_SKIP_ENVIRONMENT_PREP=1``."""
    return _truthy_env("OPSX_SKIP_ENVIRONMENT_PREP")


def environment_prep_attempts(state: GraphState) -> int:
    raw = state.get("environment_prep_attempts")
    if isinstance(raw, int) and raw >= 0:
        return raw
    return 0


def _classify_observation(observation: str) -> str:
    lower = observation.lower()
    if "hermeticboundary" in lower or "production repo cannot be registered" in lower:
        return "hermetic_boundary"
    m = _FIXTURE_NOT_FOUND.search(observation)
    if m:
        name = m.group(1).lower()
        if name in _SHARED_FIXTURE_NAMES or "fixture" in lower:
            return "shared_fixtures"
    if _KEY_ERROR.search(observation) or "keyerror" in lower:
        return "bootstrap_keys"
    if "test_database_url" in lower or "ingressstorageerror" in lower:
        return "pg_env"
    if "importerror while loading conftest" in lower:
        return "generic_collection"
    if "syntaxerror" in lower or "indentationerror" in lower or "invalid syntax" in lower:
        return "syntax_collection"
    if "error collecting" in lower or "errors during collection" in lower:
        return "generic_collection"
    if "collection" in lower and is_pytest_collection_line(observation):
        return "generic_collection"
    return "generic_collection"


def is_pytest_collection_line(observation: str) -> bool:
    """Строка pytest diagnostics относится к collection harness."""
    lower = observation.lower()
    if observation.startswith("pytest:"):
        if "collection" in lower or "fixture" in lower or "conftest" in lower:
            return True
        if "error" in lower and ".py::" not in observation:
            return True
    return False


def detect_environment_prep_cohorts(state: GraphState) -> EnvironmentPrepCohorts:
    """Сгруппировать WF_PYTEST_COLLECTION findings по kind."""
    findings = findings_from_gate_state(state)
    collection_findings = [f for f in findings if is_harness_blocker(f.repair_workflow)]
    buckets: dict[str, list[Any]] = {}
    for finding in collection_findings:
        kind = _classify_observation(finding.observation)
        buckets.setdefault(kind, []).append(finding)
    cohorts: list[EnvironmentPrepCohort] = []
    for kind, group in sorted(
        buckets.items(),
        key=lambda item: (_KIND_PRIORITY.get(item[0], 99), item[0]),
    ):
        cohorts.append(
            EnvironmentPrepCohort(
                cohort_id=f"ENV-{kind}",
                kind=kind,  # type: ignore[arg-type]
                priority=_KIND_PRIORITY.get(kind, 99),
                finding_ids=[f.finding_id for f in group],
                sample_observations=[f.observation[:500] for f in group[:5]],
            ),
        )
    return EnvironmentPrepCohorts(
        cohorts=cohorts,
        collection_finding_count=len(collection_findings),
    )


def _has_priority_cohort(cohorts: EnvironmentPrepCohorts) -> bool:
    priority_kinds = {"hermetic_boundary", "shared_fixtures"}
    return any(c.kind in priority_kinds for c in cohorts.cohorts)


def should_run_environment_prep(state: GraphState) -> bool:
    """Нужен ли узел environment_prep после group_error."""
    if skip_environment_prep_enabled():
        return False
    if str(state.get("environment_prep_execution_status") or "") == "skipped_no_agent":
        return False
    if str(state.get("quality_gate_verdict") or "") != "FAIL":
        return False
    if not isinstance(state.get("analysis_result"), dict):
        return False
    if environment_prep_attempts(state) >= MAX_ENVIRONMENT_PREP_ATTEMPTS:
        return False
    cohorts = detect_environment_prep_cohorts(state)
    if not cohorts.cohorts:
        return False
    if _has_priority_cohort(cohorts):
        return True
    return cohorts.collection_finding_count >= _COLLECTION_FINDING_THRESHOLD


def should_rerun_quality_gates_after_prep(state: GraphState) -> bool:
    """Перезапустить gates после prep (FIXED/PARTIAL + recommend)."""
    raw = state.get("environment_prep_result")
    if not isinstance(raw, dict):
        return False
    if not raw.get("gates_rerun_recommended"):
        return False
    status = str(raw.get("status") or "")
    if status not in {"FIXED", "PARTIAL"}:
        return False
    return environment_prep_attempts(state) <= MAX_ENVIRONMENT_PREP_ATTEMPTS


__all__ = [
    "MAX_ENVIRONMENT_PREP_ATTEMPTS",
    "detect_environment_prep_cohorts",
    "environment_prep_attempts",
    "should_run_environment_prep",
    "should_rerun_quality_gates_after_prep",
    "skip_environment_prep_enabled",
]
