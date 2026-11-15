from __future__ import annotations

import json
from pathlib import Path

from workflows.repair.preflight import (
    _poetry_spec,
    _safe_test_env,
    project_test_dependency_specs,
)
from workflows.repair.schemas import RepairState
from workflows.repair.schemas.preflight import (
    IntegrationCapabilities,
    PreflightReport,
    TestRuntime,
)


def test_plain_poetry_version_becomes_exact_pip_pin():
    assert _poetry_spec("temporalio", "1.33.0") == "temporalio==1.33.0"


def test_comparator_poetry_version_is_preserved():
    assert _poetry_spec("pytest", ">=8,<10") == "pytest>=8,<10"


def test_python_constraint_is_not_a_pip_dependency():
    assert _poetry_spec("python", ">=3.11,<3.15") is None


def test_pyproject_dependency_export_contains_app_and_test_deps(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.poetry.dependencies]
python = ">=3.11,<3.15"
sqlalchemy = { extras = ["asyncio"], version = ">=2,<3" }
fastapi = ">=0.115,<1"

[tool.poetry.group.test.dependencies]
pytest = ">=8,<10"
temporalio = "1.33.0"
""",
        encoding="utf-8",
    )
    specs = project_test_dependency_specs(tmp_path)
    assert "sqlalchemy[asyncio]>=2,<3" in specs
    assert "fastapi>=0.115,<1" in specs
    assert "pytest>=8,<10" in specs
    assert "temporalio==1.33.0" in specs
    assert all(not item.startswith("python") for item in specs)


def test_safe_test_env_separates_destructive_and_app_database(tmp_path):
    env = _safe_test_env(tmp_path)
    assert ":55432/" in env["TEST_DATABASE_URL"]
    assert ":5433/" in env["DATABASE_URL"]
    assert env["TEST_DATABASE_URL"] != env["DATABASE_URL"]
    assert env["STROY_ALLOW_DESTRUCTIVE_TEST_DB"] == "1"
    assert env["REDIS_PORT"] == "6390"
    assert env["CLICKHOUSE_HTTP_URL"].endswith(":8124")


def test_preflight_report_serializes_machine_readable_capabilities(tmp_path):
    runtime = TestRuntime(
        python_executable="python",
        pytest_prefix=["python", "run_pytest.py"],
        python_version="3.12",
        unit_collection_ready=True,
        unit_smoke_ready=True,
    )
    report = PreflightReport(
        status="READY",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        isolated_workspace=False,
        model_id="claude-haiku-5-5",
        reasoning_effort="low",
        model_route_ready=True,
        cursor_auth_ready=True,
        unit_ready=True,
        writable_workspace=True,
        git_ready=True,
        disk_free_bytes=10_000_000_000,
        test_runtime=runtime,
        integration=IntegrationCapabilities(ready=False),
    )
    wire = json.loads(report.model_dump_json())
    assert wire["unit_ready"] is True
    assert wire["integration"]["ready"] is False
    assert wire["test_runtime"]["pytest_prefix"] == [
        "python",
        "run_pytest.py",
    ]


def test_repair_state_preflight_defaults(tmp_path):
    state = RepairState(
        mission_id="m",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(tmp_path / "run"),
    )
    assert state.prepare_dependencies is True
    assert state.prepare_integration is False
    assert state.require_integration is False
    assert state.preflight is None
