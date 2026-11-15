"""Deterministic environment preparation and readiness gate.

No LLM is used here. The preflight proves that the isolated dogfood workspace
can actually execute project tests before CLASSIFIER/RED agents receive it.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from .schemas import RepairState
from .schemas.preflight import (
    IntegrationCapabilities,
    PreflightCheck,
    PreflightReport,
    TestRuntime,
)

MIN_FREE_BYTES = 1 * 1024**3
WARN_FREE_BYTES = 5 * 1024**3

_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "sqlalchemy": "sqlalchemy",
    "asyncpg": "asyncpg",
    "pydantic-settings": "pydantic_settings",
    "python-dotenv": "dotenv",
    "alembic": "alembic",
    "phonenumbers": "phonenumbers",
    "cryptography": "cryptography",
    "httpx": "httpx",
    "prometheus-client": "prometheus_client",
    "aiogram": "aiogram",
    "maxapi": "maxapi",
    "pytest": "pytest",
    "pytest-asyncio": "pytest_asyncio",
    "pyyaml": "yaml",
    "temporalio": "temporalio",
    "pytest-xdist": "xdist",
}

_BASE_REQUIRED_IMPORTS = (
    "pydantic",
    "langgraph",
    "pytest",
)

_ADAPTER_RUNTIME_IMPORT_TAG = "adapter_runtime"


def _check(
    check_id: str,
    status: Literal["PASS", "WARN", "FAIL", "SKIP"],
    detail: str,
    *,
    command: Iterable[str] = (),
    exit_code: int | None = None,
    duration_ms: int | None = None,
) -> PreflightCheck:
    return PreflightCheck(
        id=check_id,
        status=status,
        detail=detail,
        command=list(command),
        exit_code=exit_code,
        duration_ms=duration_ms,
    )


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: float = 120,
) -> tuple[int, str, str, int]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        duration = int((time.monotonic() - started) * 1000)
        return proc.returncode, proc.stdout, proc.stderr, duration
    except subprocess.TimeoutExpired as exc:
        duration = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr + "\nTIMEOUT", duration


def _tail(text: str, limit: int = 3500) -> str:
    clean = text.strip()
    return clean[-limit:] if len(clean) > limit else clean


def _poetry_spec(name: str, value: Any) -> str | None:
    if name == "python":
        return None
    if isinstance(value, str):
        version = value.strip()
        if version and version[0].isdigit():
            version = "==" + version
        return f"{name}{version}"
    if not isinstance(value, Mapping):
        return name
    version = str(value.get("version") or "")
    extras = value.get("extras") or []
    suffix = f"[{','.join(str(x) for x in extras)}]" if extras else ""
    return f"{name}{suffix}{version}"


def runtime_dependency_specs(repo: Path) -> list[str]:
    path = resource_path(repo, "tools", "langgraph_runtime", "requirements.txt")
    if not path.is_file():
        return [
            "langgraph>=1.1,<2",
            "langgraph-checkpoint-sqlite>=3,<4",
            "cursor-sdk>=1,<2",
            "pytest>=8,<10",
        ]
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def project_test_dependency_specs(repo: Path) -> list[str]:
    data = tomllib.loads((resource_path(repo, "pyproject.toml")).read_text(encoding="utf-8"))
    poetry = data.get("tool", {}).get("poetry", {})
    specs: list[str] = []
    for name, value in (poetry.get("dependencies") or {}).items():
        spec = _poetry_spec(str(name), value)
        if spec:
            specs.append(spec)
    test = (
        poetry.get("group", {})
        .get("test", {})
        .get("dependencies", {})
    )
    for name, value in test.items():
        spec = _poetry_spec(str(name), value)
        if spec:
            specs.append(spec)
    # Deterministic order, preserving first occurrence.
    return list(dict.fromkeys(specs))


def _adapter_runtime_importable(python: Path, repo: Path) -> bool:
    """Проверить wheel адаптера через neutral registry (bootstrap hook провайдера)."""
    runtime = resource_path(repo, "opsx", "opsx_runtime")
    if not runtime.is_dir():
        return False
    script = (
        "import sys,json;"
        f"sys.path.insert(0,{str(runtime)!r});"
        "ok=False\n"
        "err=''\n"
        "try:\n"
        " from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered\n"
        " from agents.adapters.provider_registry import call_bootstrap_hook\n"
        " ensure_adapter_factories_registered()\n"
        f" call_bootstrap_hook('ensure_wheel_importable', repo_root={str(repo)!r})\n"
        " ok=True\n"
        "except Exception as exc:\n"
        " err=f'{type(exc).__name__}: {exc}'\n"
        "print(json.dumps({'ok':ok,'err':err}))"
    )
    code, out, _, _ = _run(
        [str(python), "-c", script],
        cwd=repo,
        timeout=45,
    )
    if code != 0:
        return False
    try:
        payload = json.loads(out.strip() or "{}")
    except Exception:
        return False
    return bool(payload.get("ok"))


def _missing_imports(python: Path, repo: Path) -> list[str]:
    settings_path = repo / "opsx/project/project.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    names = sorted(set(_BASE_REQUIRED_IMPORTS) | set(settings.get("preflight_imports", [])))
    script = (
        "import importlib.util,json;"
        f"mods={json.dumps(names)};"
        "print(json.dumps([m for m in mods if importlib.util.find_spec(m) is None]))"
    )
    code, out, _, _ = _run(
        [str(python), "-c", script],
        cwd=repo,
        timeout=30,
    )
    if code != 0:
        missing = names
    else:
        try:
            missing = list(json.loads(out.strip() or "[]"))
        except Exception:
            missing = names
    if not _adapter_runtime_importable(python, repo):
        missing.append(_ADAPTER_RUNTIME_IMPORT_TAG)
    return missing


def ensure_dependencies(
    python: Path,
    repo: Path,
    *,
    prepare: bool,
) -> tuple[bool, list[PreflightCheck], list[str]]:
    checks: list[PreflightCheck] = []
    actions: list[str] = []
    missing = _missing_imports(python, repo)
    if not missing:
        checks.append(
            _check(
                "python_dependencies",
                "PASS",
                "LangGraph/adapter runtime and project/test imports are available",
            )
        )
        return True, checks, actions

    if not prepare:
        checks.append(
            _check(
                "python_dependencies",
                "FAIL",
                "missing imports: " + ", ".join(missing),
            )
        )
        return False, checks, actions

    specs = list(
        dict.fromkeys(
            [*runtime_dependency_specs(repo), *project_test_dependency_specs(repo)]
        )
    )
    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        *specs,
    ]
    code, out, err, duration = _run(
        command,
        cwd=repo,
        timeout=900,
    )
    detail = _tail(out + "\n" + err)
    checks.append(
        _check(
            "prepare_project_test_dependencies",
            "PASS" if code == 0 else "FAIL",
            detail or ("dependencies installed" if code == 0 else "pip failed"),
            command=command,
            exit_code=code,
            duration_ms=duration,
        )
    )
    if code != 0:
        return False, checks, actions

    actions.append("installed project + test dependencies into repair runtime")
    missing_after = _missing_imports(python, repo)
    if missing_after:
        checks.append(
            _check(
                "python_dependencies",
                "FAIL",
                "imports still missing after pip install: "
                + ", ".join(missing_after),
            )
        )
        return False, checks, actions
    checks.append(
        _check(
            "python_dependencies",
            "PASS",
            "all required imports available after preparation",
        )
    )
    return True, checks, actions


def _safe_test_env(workspace: Path) -> dict[str, str]:
    env = dict(os.environ)
    paths = [str(workspace), str(workspace / "src"), str(resource_path(workspace, ".cursor/pipeline"))]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    settings_path = workspace / "opsx/project/project.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    for key, value in settings.get("test_environment", {}).items():
        env[str(key)] = str(value)
    return env


def _write_pytest_wrapper(
    state: RepairState,
    python: Path,
    env: Mapping[str, str],
) -> Path:
    target = Path(state.output_dir) / "run_pytest.py"
    workspace = Path(state.workspace).resolve()
    selected_env = {
        key: env[key]
        for key in (
            "PYTHONPATH",
            "STROY_ADMIN_ALERTS_SINK",
            "TEST_DATABASE_URL",
            "DATABASE_URL",
            "STROY_ALLOW_DESTRUCTIVE_TEST_DB",
            "REDIS_HOST",
            "REDIS_PORT",
            "CLICKHOUSE_HTTP_URL",
            "ALLOW_DESTRUCTIVE_TEST_CLICKHOUSE",
            "INGEST_API_KEY",
        )
        if key in env
    }
    source = (
        "import os,sys\n"
        "from pathlib import Path\n"
        f"WORKSPACE=Path({str(workspace)!r})\n"
        f"ENV={selected_env!r}\n"
        "os.environ.update(ENV)\n"
        "os.chdir(WORKSPACE)\n"
        "sys.path[:0]=[str(WORKSPACE),str(WORKSPACE/'src'),"
        "str(WORKSPACE/'.cursor'/'pipeline')]\n"
        "import pytest\n"
        "raise SystemExit(pytest.main(sys.argv[1:]))\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def _test_runtime_checks(
    state: RepairState,
    python: Path,
) -> tuple[TestRuntime, list[PreflightCheck], list[str]]:
    workspace = Path(state.workspace).resolve()
    run_dir = Path(state.output_dir)
    env = _safe_test_env(workspace)
    wrapper = _write_pytest_wrapper(state, python, env)
    prefix = [str(python), str(wrapper)]
    checks: list[PreflightCheck] = []
    actions = [f"created deterministic pytest wrapper: {wrapper}"]

    version_cmd = [str(python), "-c", "import sys; print(sys.version)"]
    code, out, err, duration = _run(
        version_cmd,
        cwd=workspace,
        env=env,
        timeout=20,
    )
    version = (out or err).strip().splitlines()[0] if (out or err).strip() else ""
    checks.append(
        _check(
            "python_runtime",
            "PASS" if code == 0 and (3, 11) <= sys.version_info < (3, 15) else "FAIL",
            version,
            command=version_cmd,
            exit_code=code,
            duration_ms=duration,
        )
    )

    collection_cmd = [*prefix, "--collect-only", "-q", "tests/unit"]
    code, out, err, duration = _run(
        collection_cmd,
        cwd=workspace,
        env=env,
        timeout=240,
    )
    lines = [
        line.strip()
        for line in out.splitlines()
        if "::" in line and line.strip().startswith("tests")
    ]
    collection_ready = code == 0 and bool(lines)
    checks.append(
        _check(
            "pytest_unit_collection",
            "PASS" if collection_ready else "FAIL",
            (
                f"collected {len(lines)} node ids; first={lines[0]}"
                if collection_ready
                else _tail(out + "\n" + err)
            ),
            command=collection_cmd,
            exit_code=code,
            duration_ms=duration,
        )
    )

    smoke = run_dir / "preflight_smoke_test.py"
    smoke.write_text(
        "import pytest\n"
        "def test_repair_preflight_runtime():\n"
        "    assert callable(pytest.main)\n",
        encoding="utf-8",
    )
    smoke_cmd = [*prefix, "-q", str(smoke), "--maxfail=1"]
    code, out, err, duration = _run(
        smoke_cmd,
        cwd=workspace,
        env=env,
        timeout=90,
    )
    smoke_ready = code == 0
    checks.append(
        _check(
            "pytest_unit_smoke",
            "PASS" if smoke_ready else "FAIL",
            _tail(out + "\n" + err),
            command=smoke_cmd,
            exit_code=code,
            duration_ms=duration,
        )
    )

    runtime = TestRuntime(
        python_executable=str(python),
        pytest_prefix=prefix,
        python_version=version,
        unit_collection_ready=collection_ready,
        unit_smoke_ready=smoke_ready,
        first_unit_nodeid=lines[0] if lines else None,
        env={
            key: value
            for key, value in env.items()
            if key
            in {
                "PYTHONPATH",
                "STROY_ADMIN_ALERTS_SINK",
                "TEST_DATABASE_URL",
                "DATABASE_URL",
                "STROY_ALLOW_DESTRUCTIVE_TEST_DB",
                "REDIS_HOST",
                "REDIS_PORT",
                "CLICKHOUSE_HTTP_URL",
                "ALLOW_DESTRUCTIVE_TEST_CLICKHOUSE",
            }
        },
    )
    return runtime, checks, actions


def _tcp_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except OSError:
        return False


def _docker_daemon(workspace: Path) -> tuple[bool, PreflightCheck]:
    command = ["docker", "info", "--format", "{{.ServerVersion}}"]
    if shutil.which("docker") is None:
        return False, _check(
            "docker_daemon",
            "WARN",
            "docker CLI not found; integration capability unavailable",
        )
    code, out, err, duration = _run(
        command,
        cwd=workspace,
        timeout=12,
    )
    return code == 0, _check(
        "docker_daemon",
        "PASS" if code == 0 else "WARN",
        (out or err).strip(),
        command=command,
        exit_code=code,
        duration_ms=duration,
    )


def _start_docker_desktop(workspace: Path) -> PreflightCheck | None:
    candidates = [
        Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Docker"
        / "Docker"
        / "Docker Desktop.exe",
    ]
    exe = next((item for item in candidates if item.is_file()), None)
    if exe is None:
        return None
    started = time.monotonic()
    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(workspace),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return _check(
            "docker_desktop_start",
            "WARN",
            f"failed to start Docker Desktop: {type(exc).__name__}: {exc}",
        )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ready, _ = _docker_daemon(workspace)
        if ready:
            return _check(
                "docker_desktop_start",
                "PASS",
                "Docker Desktop daemon became ready",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        time.sleep(2)
    return _check(
        "docker_desktop_start",
        "WARN",
        "Docker Desktop launched but daemon did not become ready within 90s",
        duration_ms=int((time.monotonic() - started) * 1000),
    )




def _integration_capabilities(
    state: RepairState, python: Path, test_env: Mapping[str, str],
) -> tuple[IntegrationCapabilities, list[PreflightCheck], list[str]]:
    workspace = Path(state.workspace).resolve()
    profile = workspace / "opsx/project/infra/infra-profile.yaml"
    return IntegrationCapabilities(), [_check(
        "project_infrastructure", "WARN",
        "Infrastructure readiness is owned by the project infra_tests stage; "
        + ("project profile found" if profile.is_file() else "project profile not configured"),
    )], []




async def _cursor_model_check(
    state: RepairState,
) -> tuple[bool, bool, list[PreflightCheck]]:
    checks: list[PreflightCheck] = []
    try:
        from cursor_executor.auth import resolve_auth
        from cursor_executor.preflight import (
            PreflightFailure,
            validate_model_route,
        )
        from cursor_executor.schemas import ModelRoute
        from cursor_executor.sdk_client import AsyncCursorSdkClient
    except Exception as exc:
        checks.append(
            _check(
                "adapter_preflight_import",
                "FAIL",
                f"{type(exc).__name__}: {exc}",
            )
        )
        return False, False, checks

    auth = resolve_auth()
    if not auth.api_key:
        checks.append(
            _check(
                "cursor_auth",
                "FAIL",
                "CURSOR_API_KEY is not available",
            )
        )
        return False, False, checks
    checks.append(
        _check(
            "cursor_auth",
            "PASS",
            f"Cursor auth resolved from {auth.source}",
        )
    )

    route = ModelRoute(
        model_id=state.model.id,
        params={
            str(item.id): str(item.value)
            for item in state.model.params
        },
    )
    try:
        catalog = await AsyncCursorSdkClient().list_models(
            api_key=auth.api_key,
            workspace=state.workspace,
        )
    except Exception as exc:
        checks.append(
            _check(
                "cursor_model_catalog",
                "FAIL",
                f"{type(exc).__name__}: {exc}",
            )
        )
        return True, False, checks

    validation = validate_model_route(route, catalog)
    if isinstance(validation, PreflightFailure):
        checks.append(
            _check(
                "cursor_model_route",
                "FAIL",
                validation.message,
            )
        )
        return True, False, checks

    checks.append(
        _check(
            "cursor_model_route",
            "PASS",
            (
                f"{state.model.id} "
                + ",".join(
                    f"{item.id}={item.value}"
                    for item in state.model.params
                )
            ),
        )
    )
    return True, True, checks


async def run_preflight(
    raw_state: RepairState | Mapping[str, Any],
) -> dict[str, Any]:
    state = (
        raw_state
        if isinstance(raw_state, RepairState)
        else RepairState.model_validate(raw_state)
    )
    repo = Path(state.repository_path).resolve()
    workspace = Path(state.workspace).resolve()
    checks: list[PreflightCheck] = []
    actions: list[str] = []
    blocking: list[str] = []

    if not repo.is_dir() or not (resource_path(repo, "pyproject.toml")).is_file():
        blocking.append("repository/pyproject.toml missing")
    if not workspace.is_dir() or not (resource_path(workspace, "pyproject.toml")).is_file():
        blocking.append("workspace/pyproject.toml missing")

    checks.append(
        _check(
            "workspace_root",
            "PASS",
            f"repository={repo}; workspace={workspace}",
        )
    )

    git_ready = False
    if workspace.is_dir():
        command = ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"]
        code, out, err, duration = _run(
            command,
            cwd=workspace,
            timeout=20,
        )
        top = out.strip()
        git_ready = code == 0 and Path(top).resolve() == workspace
        checks.append(
            _check(
                "git_workspace",
                "PASS" if git_ready else "FAIL",
                top or err.strip(),
                command=command,
                exit_code=code,
                duration_ms=duration,
            )
        )
    if not git_ready:
        blocking.append("workspace is not a valid Git worktree/clone")

    writable = False
    if workspace.is_dir():
        probe = resource_path(workspace, "opsx", ".repair-preflight-write-probe")
        try:
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok\n", encoding="utf-8")
            writable = probe.read_text(encoding="utf-8") == "ok\n"
        except Exception as exc:
            checks.append(
                _check(
                    "workspace_writable",
                    "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass
    if writable:
        checks.append(
            _check(
                "workspace_writable",
                "PASS",
                "isolated workspace write/delete probe succeeded",
            )
        )
    else:
        blocking.append("workspace is not writable")

    free_bytes = shutil.disk_usage(workspace if workspace.exists() else repo).free
    disk_status = (
        "FAIL"
        if free_bytes < MIN_FREE_BYTES
        else "WARN"
        if free_bytes < WARN_FREE_BYTES
        else "PASS"
    )
    checks.append(
        _check(
            "disk_free",
            disk_status,
            f"free_bytes={free_bytes}",
        )
    )
    if free_bytes < MIN_FREE_BYTES:
        blocking.append("less than 1 GiB free disk space")

    python = Path(sys.executable).resolve()
    deps_ok, dep_checks, dep_actions = await asyncio.to_thread(
        ensure_dependencies,
        python,
        workspace,
        prepare=state.prepare_dependencies,
    )
    checks.extend(dep_checks)
    actions.extend(dep_actions)
    if not deps_ok:
        blocking.append("project/test dependencies unavailable")

    # Cursor executor package lives outside installed site-packages.
    executor_root = resource_path(repo, "tools", "cursor_executor")
    if str(executor_root) not in sys.path:
        sys.path.insert(0, str(executor_root))
    auth_ready, model_ready, cursor_checks = await _cursor_model_check(state)
    checks.extend(cursor_checks)
    if not auth_ready:
        blocking.append("Adapter authentication unavailable")
    if not model_ready:
        blocking.append("requested Cursor model route unavailable")

    if deps_ok:
        test_runtime, test_checks, test_actions = await asyncio.to_thread(
            _test_runtime_checks,
            state,
            python,
        )
        checks.extend(test_checks)
        actions.extend(test_actions)
    else:
        test_runtime = TestRuntime(
            python_executable=str(python),
            pytest_prefix=[str(python), "-m", "pytest"],
            python_version=sys.version,
        )

    if not test_runtime.unit_collection_ready:
        blocking.append("tests/unit collection failed")
    if not test_runtime.unit_smoke_ready:
        blocking.append("pytest unit smoke failed")

    integration, integration_checks, integration_actions = await asyncio.to_thread(
        _integration_capabilities,
        state,
        python,
        _safe_test_env(workspace),
    )
    checks.extend(integration_checks)
    actions.extend(integration_actions)
    if state.require_integration and not integration.ready:
        blocking.append("integration environment required but not READY")

    unit_ready = (
        git_ready
        and writable
        and free_bytes >= MIN_FREE_BYTES
        and deps_ok
        and auth_ready
        and model_ready
        and test_runtime.unit_collection_ready
        and test_runtime.unit_smoke_ready
    )
    report = PreflightReport(
        status="BLOCKED" if blocking else "READY",
        repository_path=str(repo),
        workspace=str(workspace),
        isolated_workspace=state.isolated_workspace,
        model_id=state.model.id,
        reasoning_effort=next(
            (
                str(item.value)
                for item in state.model.params
                if item.id == "reasoning_effort"
            ),
            "",
        ),
        model_route_ready=model_ready,
        cursor_auth_ready=auth_ready,
        unit_ready=unit_ready,
        writable_workspace=writable,
        git_ready=git_ready,
        disk_free_bytes=free_bytes,
        test_runtime=test_runtime,
        integration=integration,
        checks=checks,
        prepared_actions=actions,
        blocking_reasons=blocking,
    )
    output = Path(state.output_dir) / "preflight.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "preflight": report,
        "status": "PREFLIGHT_READY"
        if report.status == "READY"
        else "PREFLIGHT_BLOCKED",
        "current_stage": "preflight",
        "error": None
        if report.status == "READY"
        else "; ".join(blocking),
        "revision": state.revision + 1,
    }
