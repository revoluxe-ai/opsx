"""Shared application services used by CLI and HTTP, with owned process lifecycle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import webbrowser

from .context import OpsxError, Project, safe_change_id
from .provider_settings import load_provider, validate_provider, PROVIDER_MODULES
from .storage import read_json, write_bytes, write_json
from .supervisor import WorkflowSupervisor
from .supervisor_meta import format_opsx_pytest_arg, resolve_opsx_pytest_target
from .worker_runtime import (
    resolve_worker_interpreter,
    worker_interpreter_candidates,
)
from .supervisor_timeouts import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_SUPERVISOR_TIMEOUT_MINUTES,
    DEFAULT_WORKFLOW_TIMEOUT_MINUTES,
    SupervisorTimeouts,
)

# Панель живёт в этом процессе (asyncio-задача uvicorn), не в отдельном Python.
_PANEL_SERVERS: dict[str, tuple[object, asyncio.Task]] = {}


def _interpreter_supports_panel(path: str) -> bool:
    """Проверяет, что в интерпретаторе установлены зависимости локальной панели."""
    try:
        result = subprocess.run(
            [path, "-c", "import fastapi, uvicorn"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _panel_interpreter_candidates(project: Project, installation: dict) -> list[str]:
    """OPSX runtime с fastapi/uvicorn; product ``.venv`` не подменяет LangGraph worker."""
    return worker_interpreter_candidates(project, installation)


async def resolve_panel_interpreter(project: Project, installation: dict) -> str:
    """Выбирает Python с fastapi/uvicorn для subprocess панели."""
    def pick() -> str:
        for path in _panel_interpreter_candidates(project, installation):
            if _interpreter_supports_panel(path):
                return path
        raise OpsxError(
            "PANEL_DEPS_MISSING",
            "Для opsx start нужны fastapi и uvicorn. "
            "Выполните opsx upgrade (без --no-runtime) или "
            "pip install 'opsx-cli[web]' в активном окружении.",
        )

    return await asyncio.to_thread(pick)


async def _http(state: dict, path: str, *, method: str = "GET") -> dict:
    def request() -> dict:
        url = str(state["url"])
        if not url.startswith("http://127.0.0.1:"):
            raise OpsxError("PANEL_STATE_INVALID", "Panel state is not a loopback URL.")
        req = urllib.request.Request(url + path, method=method,
                                     headers={"X-OPSX-Token": state["token"]})
        # OPSX panel is loopback-only. Never route authenticated local
        # requests via system HTTP proxy: on Windows urllib may inherit
        # proxy settings for 127.0.0.1 and return a false 503.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            return json.load(response)
    return await asyncio.to_thread(request)


async def panel_status(project: Project) -> dict:
    state = await read_json(project.panel_state, {})
    if not state:
        return {"running": False}
    try:
        health = await _http(state, "/api/health")
        if health.get("project_root") != str(project.root) or health.get("instance_id") != state.get("instance_id"):
            return {"running": False, "error": "PANEL_IDENTITY_MISMATCH"}
        return {"running": True, "url": state["url"], **health}
    except (OSError, ValueError, KeyError, OpsxError):
        return {"running": False, "last_pid": state.get("pid")}


async def start_panel(project: Project, *, port: int = 8765, browser: bool = True) -> dict:
    """Поднять панель в текущем процессе и вернуть управление, когда health отвечает.

    WorkflowSupervisor живёт в той же asyncio-задаче, что и HTTP. Отдельный
    Python для панели не запускается: его смерть обрывала супервизор и
    оставляла worker сиротой.
    """
    current = await panel_status(project)
    if current.get("running"):
        if browser:
            await asyncio.to_thread(webbrowser.open, current["url"])
        return {"status": "ALREADY_RUNNING", **current}
    installation = await read_json(project.installation)
    if not installation:
        raise OpsxError("NOT_INSTALLED", "Run opsx install first.")
    if not 1 <= port <= 65535:
        raise OpsxError("PORT_INVALID", "Port must be between 1 and 65535.")

    def assert_port_free() -> None:
        """Connect-probe: Windows SO_REUSEADDR lets bind succeed on a busy port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            busy = sock.connect_ex(("127.0.0.1", port)) == 0
        if busy:
            raise OpsxError(
                "PORT_IN_USE",
                f"127.0.0.1:{port} is already in use. "
                "Stop the old process (opsx stop does not own it) or run opsx start --port <n>.",
            )

    await asyncio.to_thread(assert_port_free)
    import uvicorn

    from .panel import create_app

    state = {
        "instance_id": uuid.uuid4().hex,
        "token": secrets.token_urlsafe(32),
        "url": f"http://127.0.0.1:{port}",
        "project_root": str(project.root),
        "pid": os.getpid(),
        "active_run_id": None,
    }
    await write_json(project.panel_state, state)
    holder: dict = {}
    app = create_app(project, state, shutdown=lambda: setattr(holder["server"], "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    holder["server"] = server

    async def _serve() -> None:
        try:
            await server.serve()
        finally:
            _PANEL_SERVERS.pop(str(project.root), None)
            await asyncio.to_thread(project.panel_state.unlink, missing_ok=True)

    task = asyncio.create_task(_serve())
    _PANEL_SERVERS[str(project.root)] = (server, task)
    for _ in range(100):
        await asyncio.sleep(0.1)
        if task.done():
            exc = task.exception()
            raise OpsxError("PANEL_START_FAILED", f"Panel exited before ready: {exc}")
        status = await panel_status(project)
        if status.get("running"):
            if browser:
                await asyncio.to_thread(webbrowser.open, state["url"])
            return {"status": "STARTED", **status}
    raise OpsxError("PANEL_START_TIMEOUT", "Panel did not become ready in this process.")


async def join_panel(project: Project) -> None:
    """Держать процесс, пока in-process панель не остановится."""
    bound = _PANEL_SERVERS.get(str(project.root))
    if bound is None:
        return
    _server, task = bound
    await task


async def foreground_start(project: Project, *, port: int = 8765, browser: bool = True) -> None:
    """Панель в этом процессе до Ctrl+C или POST /api/shutdown.

    Если fastapi есть только в другом интерпретаторе, процесс заменяется
    через exec (не остаётся родитель, который можно убить отдельно от сервера).
    """
    current = await panel_status(project)
    if current.get("running"):
        print(str(current["url"]), flush=True)
        return
    installation = await read_json(project.installation)
    if not installation:
        raise OpsxError("NOT_INSTALLED", "Run opsx install first.")
    interpreter = await resolve_panel_interpreter(project, installation)
    if Path(interpreter).resolve() != Path(sys.executable).resolve():
        argv = [interpreter, "-m", "opsx_cli", "start", "--project", str(project.root), "--port", str(port)]
        if not browser:
            argv.append("--no-browser")
        os.execve(interpreter, argv, project.environment())
    started = await start_panel(project, port=port, browser=browser)
    print(str(started.get("url") or ""), flush=True)
    await join_panel(project)


async def stop_panel(project: Project) -> dict:
    current = await panel_status(project)
    if not current.get("running"):
        return {"status": "NOT_RUNNING"}
    state = await read_json(project.panel_state)
    await _http(state, "/api/shutdown", method="POST")
    for _ in range(100):
        await asyncio.sleep(0.1)
        if not (await panel_status(project)).get("running"):
            return {"status": "STOPPED"}
    raise OpsxError("PANEL_STOP_TIMEOUT", "Owned panel did not confirm shutdown.")


class RunService:
    """One project execution lease, durable status and bounded log delivery."""
    def __init__(self, project: Project):
        self.project = project
        self.process: asyncio.subprocess.Process | None = None

    async def resolve_worker_runtime(self) -> tuple[str, dict[str, str]]:
        """Интерпретатор OPSX worker (LangGraph), не product pytest venv."""
        metadata = await read_json(self.project.installation)
        if not metadata:
            raise OpsxError("NOT_INSTALLED", "Run opsx install first.")

        def pick() -> str:
            return resolve_worker_interpreter(self.project, metadata)

        interpreter = await asyncio.to_thread(pick)
        env = self.project.environment()
        settings = await read_json(self.project.settings, {})
        for key, value in settings.get("environment", {}).items():
            if key.startswith("OPSX_") and key not in ("OPSX_PROJECT_ROOT", "OPSX_CORE_ROOT"):
                env[str(key)] = str(value)
        return interpreter, env

    async def rerun_fix_checks(
        self,
        fix_report: dict,
        *,
        timeout_seconds: float,
    ) -> list[dict]:
        """Перезапуск именованных pytest из отчёта fix-агента (контроллер, не модель)."""
        checks = fix_report.get("checks_run") or []
        if not isinstance(checks, list):
            return []
        results: list[dict] = []
        core = self.project.core
        for item in checks:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "").strip()
            if not node_id:
                continue
            try:
                test_file = resolve_opsx_pytest_target(core, node_id)
                pytest_arg = format_opsx_pytest_arg(core, node_id)
            except ValueError as exc:
                results.append({"node_id": node_id, "exit_code": 1, "error": str(exc)})
                continue
            if not test_file.is_file():
                results.append(
                    {
                        "node_id": node_id,
                        "exit_code": 4,
                        "error": f"pytest test file missing: {test_file}",
                    }
                )
                continue
            argv = [sys.executable, "-m", "pytest", pytest_arg, "-q", "--tb=short"]

            def run() -> tuple[int, str]:
                completed = subprocess.run(
                    argv,
                    cwd=core,
                    capture_output=True,
                    text=True,
                    timeout=max(30.0, timeout_seconds),
                    check=False,
                )
                tail = (completed.stdout or "")[-2000:] + (completed.stderr or "")[-2000:]
                return int(completed.returncode), tail

            code, tail = await asyncio.to_thread(run)
            results.append({"node_id": node_id, "exit_code": code, "tail": tail})
        return results

    async def execute(
        self,
        *,
        mode: str,
        change: str,
        bug: str = "",
        timeout: float | None = None,
        workflow_timeout_minutes: float = DEFAULT_WORKFLOW_TIMEOUT_MINUTES,
        supervisor_timeout_minutes: float = DEFAULT_SUPERVISOR_TIMEOUT_MINUTES,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        echo: bool = False,
        run_id: str | None = None,
        provider: str | None = None,
    ) -> dict:
        change = safe_change_id(change)
        run_id = run_id or uuid.uuid4().hex
        if mode == "workflow":
            provider = validate_provider(
                provider if provider is not None else (await asyncio.to_thread(load_provider, self.project))["provider"]
            )
            timeouts = SupervisorTimeouts.from_minutes(
                workflow_timeout_minutes=workflow_timeout_minutes,
                supervisor_timeout_minutes=supervisor_timeout_minutes,
                max_attempts=max_attempts,
            )
            supervisor = WorkflowSupervisor(
                self,
                timeouts=timeouts,
                change_id=change,
                run_id=run_id,
                echo=echo,
                provider=provider,
            )
            return await supervisor.run()
        timeout_seconds = float(timeout if timeout is not None else 2700)
        return await self._execute_single(
            mode=mode,
            change=change,
            bug=bug,
            timeout_seconds=timeout_seconds,
            echo=echo,
            run_id=run_id,
        )

    async def _execute_single(
        self,
        *,
        mode: str,
        change: str,
        bug: str,
        timeout_seconds: float,
        echo: bool,
        run_id: str,
    ) -> dict:
        from .supervisor_subprocess import run_worker_with_watchdog

        directory = self.project.runs / "controller" / run_id
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        request = {
            "schema_version": 1,
            "mode": mode,
            "change_id": change,
            "bug": bug,
            "timeout_seconds": timeout_seconds,
            "project_root": str(self.project.root),
            "core_root": str(self.project.core),
        }
        await write_json(directory / "request.json", request)
        record = {
            "run_id": run_id,
            "status": "RUNNING",
            "mode": mode,
            "change_id": change,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "log": str(directory / "output.log"),
        }
        await write_json(directory / "status.json", record)
        try:
            interpreter, env = await self.resolve_worker_runtime()
            code, hang = await run_worker_with_watchdog(
                interpreter=interpreter,
                request_path=directory / "request.json",
                log_path=directory / "output.log",
                cwd=self.project.root,
                env=env,
                wall_clock_limit=timeout_seconds,
                silence_limit=300.0,
                echo=echo,
            )
            worker_record = await read_json(directory / "status.json", record)
            if hang:
                worker_record["error"] = hang
            process_status = "COMPLETED" if code == 0 else "FAILED"
            verification = worker_record.get("verification_status") or "NOT_EVALUATED"
            workflow = worker_record.get("workflow_status") or "UNKNOWN"
            aggregate = verification if verification in {
                "PASS",
                "FAIL",
                "NOT_READY",
                "NOT_EVALUATED",
            } else ("PROCESS_COMPLETED" if code == 0 else "PROCESS_FAILED")
            record.update(
                process_exit_code=code,
                process_status=process_status,
                workflow_status=workflow,
                verification_status=verification,
                status=aggregate,
                exit_code=code
                if verification not in {"PASS", "FAIL", "NOT_READY"}
                else (0 if verification == "PASS" else 1),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            await write_json(directory / "status.json", record)
            return record
        except BaseException as exc:
            record.update(
                status="OUTCOME_UNKNOWN" if self.process and self.process.returncode is None else "FAILED",
                error=f"{type(exc).__name__}: {exc}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            await write_json(directory / "status.json", record)
            raise

    async def history(self) -> list[dict]:
        paths = await asyncio.to_thread(lambda: sorted(
            (self.project.runs / "controller").glob("*/status.json"), reverse=True))
        records = [await read_json(path) for path in paths]
        return sorted(records, key=lambda record: record.get("started_at", ""), reverse=True)
