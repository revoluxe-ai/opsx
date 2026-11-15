"""Deterministic preflight contracts for the LangGraph Bug Team."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreflightCheck(StrictModel):
    id: str
    status: Literal["PASS", "WARN", "FAIL", "SKIP"]
    detail: str
    command: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    duration_ms: int | None = None


class IntegrationCapabilities(StrictModel):
    ready: bool = False
    docker_cli: bool = False
    docker_daemon: bool = False
    disposable_postgres_55432: bool = False
    preview_postgres_5433: bool = False
    redis_6390: bool = False
    clickhouse_8124: bool = False
    disposable_db_guard_verified: bool = False
    migrations_applied: bool = False



class TestRuntime(StrictModel):
    python_executable: str
    pytest_prefix: list[str]
    python_version: str
    unit_collection_ready: bool = False
    unit_smoke_ready: bool = False
    first_unit_nodeid: str | None = None
    env: dict[str, str] = Field(default_factory=dict)


class PreflightReport(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["READY", "BLOCKED"]
    repository_path: str
    workspace: str
    isolated_workspace: bool
    model_id: str
    reasoning_effort: str
    model_route_ready: bool = False
    cursor_auth_ready: bool = False
    unit_ready: bool = False
    writable_workspace: bool = False
    git_ready: bool = False
    disk_free_bytes: int = 0
    test_runtime: TestRuntime
    integration: IntegrationCapabilities = Field(
        default_factory=IntegrationCapabilities
    )
    checks: list[PreflightCheck] = Field(default_factory=list)
    prepared_actions: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)

