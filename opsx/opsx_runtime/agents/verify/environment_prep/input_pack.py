"""Input pack для VerifyEnvironmentPrepAgent."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents.state import GraphState
from agents.verify.environment_prep.cohort_detect import detect_environment_prep_cohorts
from agents.verify.environment_prep.schemas import EnvironmentPrepCohorts

_ENV_WATCHLIST = (
    "TEST_DATABASE_URL",
    "DATABASE_URL",
    "STROY_ALLOW_INTEGRATION_PG",
    "OPSX_SKIP_ENVIRONMENT_PREP",
)


class EnvVarSnapshot(BaseModel):
    """Имя переменной и факт наличия (без значения)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    present: bool


class VerifyEnvironmentPrepInputPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    repository_path: str
    cohorts: EnvironmentPrepCohorts
    pytest_log_tail: str = ""
    pyright_issue_count: int = 0
    mypy_issue_count: int = 0
    env_snapshot: list[EnvVarSnapshot] = Field(default_factory=list)
    boundaries: str = Field(
        default=(
            "Edit tests/** conftest/fixtures/seed only. "
            "Do not weaken HermeticBoundary. Do not invent secrets."
        ),
    )


def _pytest_tail_from_state(state: GraphState) -> str:
    raw = state.get("quality_gate_raw")
    if isinstance(raw, dict):
        pytest = raw.get("pytest")
        if isinstance(pytest, dict):
            tail = str(pytest.get("stdout_tail") or "") + str(pytest.get("stderr_tail") or "")
            if tail.strip():
                return tail[-12000:]
    diag = state.get("quality_gate_diagnostics")
    if isinstance(diag, dict):
        lines = diag.get("pytest")
        if isinstance(lines, list):
            joined = "\n".join(str(x) for x in lines if x)
            return joined[-12000:]
    return ""


def _env_snapshot() -> list[EnvVarSnapshot]:
    return [
        EnvVarSnapshot(name=name, present=bool(os.environ.get(name, "").strip()))
        for name in _ENV_WATCHLIST
    ]


def build_environment_prep_input_pack(
    *,
    state: GraphState,
    repository_path: str,
    change_id: str,
) -> VerifyEnvironmentPrepInputPack:
    """Собрать pack для prompt."""
    cohorts = detect_environment_prep_cohorts(state)
    diag = state.get("quality_gate_diagnostics")
    pyright_n = 0
    mypy_n = 0
    if isinstance(diag, dict):
        pr = diag.get("pyright")
        my = diag.get("mypy")
        if isinstance(pr, list):
            pyright_n = len(pr)
        if isinstance(my, list):
            mypy_n = len(my)
    return VerifyEnvironmentPrepInputPack(
        change_id=change_id,
        repository_path=repository_path,
        cohorts=cohorts,
        pytest_log_tail=_pytest_tail_from_state(state),
        pyright_issue_count=pyright_n,
        mypy_issue_count=mypy_n,
        env_snapshot=_env_snapshot(),
    )
