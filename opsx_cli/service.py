"""Shared application services used by CLI and HTTP, with owned process lifecycle."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import webbrowser

from .context import OpsxError, Project, safe_change_id
from .storage import read_json, write_bytes, write_json

_PANEL_CHILDREN: dict[int, subprocess.Popen] = {}


async def _http(state: dict, path: str, *, method: str = "GET") -> dict:
    def request() -> dict:
        url = str(state["url"])
        if not url.startswith("http://127.0.0.1:"):
            raise OpsxError("PANEL_STATE_INVALID", "Panel state is not a loopback URL.")
        req = urllib.request.Request(url + path, method=method,
                                     headers={"X-OPSX-Token": state["token"]})
        with urllib.request.urlopen(req, timeout=2) as response:
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
    interpreter = str(project.root / installation["runtime_python"]) if installation.get("runtime_python") else sys.executable
    state = {"instance_id": uuid.uuid4().hex, "token": secrets.token_urlsafe(32),
             "url": f"http://127.0.0.1:{port}", "project_root": str(project.root)}
    await write_json(project.panel_state, state)
    log = project.runs / "panel.log"
    def launch() -> subprocess.Popen:
        with log.open("ab") as stream:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            return subprocess.Popen(
                [interpreter, "-m", "opsx_cli.panel", "--project", str(project.root), "--port", str(port)],
                cwd=project.root, env=project.environment(), stdout=stream, stderr=subprocess.STDOUT,
                creationflags=flags, start_new_session=os.name != "nt",
            )
    process = await asyncio.to_thread(launch)
    _PANEL_CHILDREN[process.pid] = process
    state["pid"] = process.pid
    await write_json(project.panel_state, state)
    for _ in range(100):
        await asyncio.sleep(0.1)
        if process.poll() is not None:
            raise OpsxError("PANEL_START_FAILED", f"Panel exited {process.returncode}; see {log}")
        status = await panel_status(project)
        if status.get("running"):
            if browser:
                await asyncio.to_thread(webbrowser.open, state["url"])
            return {"status": "STARTED", **status}
    raise OpsxError("PANEL_START_TIMEOUT", f"Panel did not become ready; see {log}")


async def stop_panel(project: Project) -> dict:
    current = await panel_status(project)
    if not current.get("running"):
        return {"status": "NOT_RUNNING"}
    state = await read_json(project.panel_state)
    await _http(state, "/api/shutdown", method="POST")
    for _ in range(100):
        await asyncio.sleep(0.1)
        if not (await panel_status(project)).get("running"):
            child = _PANEL_CHILDREN.pop(state.get("pid"), None)
            if child is not None:
                await asyncio.to_thread(child.wait, timeout=5)
            await asyncio.to_thread(project.panel_state.unlink, missing_ok=True)
            return {"status": "STOPPED"}
    raise OpsxError("PANEL_STOP_TIMEOUT", "Owned panel did not confirm shutdown.")


class RunService:
    """One project execution lease, durable status and bounded log delivery."""
    def __init__(self, project: Project):
        self.project = project
        self.process: asyncio.subprocess.Process | None = None

    async def execute(self, *, mode: str, change: str, bug: str = "",
                      timeout: float = 2700, echo: bool = False, run_id: str | None = None) -> dict:
        change = safe_change_id(change)
        metadata = await read_json(self.project.installation)
        if not metadata:
            raise OpsxError("NOT_INSTALLED", "Run opsx install first.")
        interpreter = metadata.get("runtime_python")
        if not interpreter:
            raise OpsxError("RUNTIME_NOT_INSTALLED", "Install the runtime with opsx upgrade.")
        interpreter = str(self.project.root / interpreter)
        run_id = run_id or uuid.uuid4().hex
        directory = self.project.runs / "controller" / run_id
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        request = {"schema_version": 1, "mode": mode, "change_id": change, "bug": bug,
                   "timeout_seconds": timeout, "project_root": str(self.project.root),
                   "core_root": str(self.project.core)}
        await write_json(directory / "request.json", request)
        record = {"run_id": run_id, "status": "RUNNING", "mode": mode, "change_id": change,
                  "started_at": datetime.now(timezone.utc).isoformat(), "log": str(directory / "output.log")}
        await write_json(directory / "status.json", record)
        try:
            env = self.project.environment()
            settings = await read_json(self.project.settings, {})
            for key, value in settings.get("environment", {}).items():
                if key.startswith("OPSX_") and key not in ("OPSX_PROJECT_ROOT", "OPSX_CORE_ROOT"):
                    env[str(key)] = str(value)
            self.process = await asyncio.create_subprocess_exec(
                interpreter, "-m", "opsx_cli.worker", str(directory / "request.json"),
                cwd=self.project.root, env=env, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            record["pid"] = self.process.pid
            await write_json(directory / "status.json", record)
            assert self.process.stdout is not None
            while data := await self.process.stdout.read(65536):
                def append() -> None:
                    with (directory / "output.log").open("ab") as stream:
                        stream.write(data)
                await asyncio.to_thread(append)
                if echo:
                    await asyncio.to_thread(sys.stdout.write, data.decode("utf-8", "replace"))
                    await asyncio.to_thread(sys.stdout.flush)
            code = await self.process.wait()
            record.update(status="PASS" if code == 0 else "FAILED", exit_code=code,
                          finished_at=datetime.now(timezone.utc).isoformat())
            await write_json(directory / "status.json", record)
            return record
        except BaseException as exc:
            record.update(status="OUTCOME_UNKNOWN" if self.process and self.process.returncode is None else "FAILED",
                          error=f"{type(exc).__name__}: {exc}",
                          finished_at=datetime.now(timezone.utc).isoformat())
            await write_json(directory / "status.json", record)
            raise

    async def history(self) -> list[dict]:
        paths = await asyncio.to_thread(lambda: sorted(
            (self.project.runs / "controller").glob("*/status.json"), reverse=True))
        records = [await read_json(path) for path in paths]
        return sorted(records, key=lambda record: record.get("started_at", ""), reverse=True)
