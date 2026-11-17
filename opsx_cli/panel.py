"""Async local HTTP frontend over the same application services as the CLI."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
import uvicorn

from .changes_catalog import list_openspec_changes
from .context import OpsxError, Project
from .display_time import format_run_record_times
from .service import RunService
from .site_status import site_shows_error
from .storage import read_json, write_json


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    workflow_timeout_minutes: float = Field(default=45, gt=0)
    supervisor_timeout_minutes: float = Field(default=180, gt=0)
    max_attempts: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def _validate_timeouts(self) -> WorkflowRequest:
        if self.supervisor_timeout_minutes < self.workflow_timeout_minutes:
            raise ValueError("supervisor_timeout_minutes must be >= workflow_timeout_minutes")
        return self


def create_app(project: Project, state: dict, *, shutdown=None) -> FastAPI:
    """Compose one project frontend with explicit state and shutdown ownership."""
    service = RunService(project)
    tasks: set[asyncio.Task] = set()
    app = FastAPI(title="OPSX", docs_url=None, redoc_url=None, openapi_url=None)

    def completed(done):
        tasks.discard(done)
        if not tasks:
            state["active_run_id"] = None
        if not done.cancelled():
            done.exception()  # Durable run status owns the outcome.

    _ASSISTANT_CHUNK = re.compile(
        r"\] assistant: [^\s]{1,40}\s*$",
        re.IGNORECASE,
    )

    def _emit_log_line(line: str, *, compact: bool) -> tuple[str, dict] | None:
        """Сформировать SSE-событие для строки output.log; compact отбрасывает шум stream."""
        if compact:
            low = line.lower()
            if " thinking:" in low or " thinkingδ:" in low or "thinking-delta" in low:
                return None
            if '"kind": "thinking"' in line or '"kind": "usage"' in line:
                return None
            if _ASSISTANT_CHUNK.search(line):
                return None
        event, payload = "log", {"text": line}
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict) and isinstance(parsed.get("opsx_event"), dict):
                payload = parsed["opsx_event"]
                event = str(payload.get("event") or "stage")
                if compact and event in {"thinking", "usage"}:
                    return None
        except ValueError:
            payload = {"text": line}
        return event, payload

    async def run_events(
        run_id: str,
        task=None,
        after: int = 0,
        *,
        tail_lines: int = 0,
        compact: bool = False,
    ):
        """Replay complete log lines and then observe the same durable run."""
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        directory = project.runs / "controller" / run_id
        offset, sequence, pending = 0, 0, b""
        yield "event: run_started\ndata: " + json.dumps({"run_id": run_id}) + "\n\n"

        record = await read_json(directory / "status.json", {})
        terminal = record.get("status") in {"PASS", "FAILED"}
        if terminal and after == 0 and tail_lines > 0:

            def read_file_tail(max_lines: int) -> tuple[int, list[str]]:
                path = directory / "output.log"
                if not path.is_file():
                    return 0, []
                data = path.read_bytes()
                lines = data.splitlines()
                if max_lines > 0 and len(lines) > max_lines:
                    lines = lines[-max_lines:]
                text_lines = [
                    line.decode("utf-8", "replace").rstrip("\r") for line in lines if line.strip()
                ]
                return len(data), text_lines

            offset, initial = await asyncio.to_thread(read_file_tail, tail_lines)
            for line in initial:
                sequence += 1
                if sequence <= after:
                    continue
                parsed = _emit_log_line(line, compact=compact)
                if parsed is None:
                    continue
                event, payload = parsed
                yield f"id: {sequence}\nevent: {event}\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

        while True:
            def read_tail():
                path = directory / "output.log"
                if not path.is_file():
                    return b""
                with path.open("rb") as stream:
                    stream.seek(offset)
                    return stream.read(65536)
            data = await asyncio.to_thread(read_tail)
            offset += len(data)
            pending += data
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                sequence += 1
                if sequence <= after:
                    continue
                line = raw.decode("utf-8", "replace").rstrip("\r")
                if not line:
                    continue
                parsed = _emit_log_line(line, compact=compact)
                if parsed is None:
                    continue
                event, payload = parsed
                yield f"id: {sequence}\nevent: {event}\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            record = await read_json(directory / "status.json", {})
            terminal = record.get("status") in {"PASS", "FAILED"}
            if not data and (terminal or task is not None and task.done()):
                if pending:
                    sequence += 1
                    if sequence > after:
                        yield f"id: {sequence}\nevent: log\ndata: " + json.dumps({"text": pending.decode("utf-8", "replace")}) + "\n\n"
                if task is not None:
                    try:
                        record = await task
                    except Exception as exc:
                        record = {
                            "status": "FAILED",
                            "error": str(exc),
                            "supervisor_stop": "EXCEPTION",
                            "site_error": True,
                        }
                record = dict(record)
                record["site_error"] = site_shows_error(record)
                yield "event: result\ndata: " + json.dumps(record, ensure_ascii=False) + "\n\n"
                yield 'event: done\ndata: {}\n\n'
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    @app.middleware("http")
    async def authorize(request: Request, call_next):
        if request.headers.get("host") != urlsplit(state["url"]).netloc:
            return HTMLResponse("Host rejected", status_code=400)
        if request.url.path == "/":
            response = await call_next(request)
            response.set_cookie("opsx_token", state["token"], httponly=True, samesite="strict")
            return response
        token = request.headers.get("X-OPSX-Token") or request.cookies.get("opsx_token", "")
        if not secrets.compare_digest(token, state["token"]):
            return HTMLResponse("Unauthorized", status_code=401)
        origin = request.headers.get("origin")
        if origin and origin != state["url"]:
            return HTMLResponse("Origin rejected", status_code=403)
        try:
            return await call_next(request)
        except OpsxError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse({"code": exc.code, "message": str(exc)}, status_code=409)

    @app.get("/", response_class=HTMLResponse)
    async def home():
        path = Path(__file__).parent / "static/index.html"
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    @app.get("/api/health")
    async def health():
        return {"instance_id": state["instance_id"], "project_root": str(project.root),
                "pid": state.get("pid"), "active": bool(tasks)}

    @app.get("/api/project")
    async def settings():
        return await read_json(project.settings, {})

    @app.get("/api/runs")
    async def history():
        records = await service.history()
        return {"runs": [format_run_record_times(record) for record in records]}

    @app.get("/api/changes")
    async def changes():
        """OpenSpec change-id каталога проекта для выбора в UI."""
        return {"changes": await asyncio.to_thread(list_openspec_changes, project.root)}

    @app.get("/runs/active")
    async def active():
        """Активный run только пока workflow-задача жива в этом процессе панели.

        Статус RUNNING на диске после перезапуска панели — не активный run (зомби).
        """
        if not tasks:
            return {"run": None}
        run_id = str(state.get("active_run_id") or "").strip()
        if not run_id:
            return {"run": None}
        record = await read_json(project.runs / "controller" / run_id / "status.json", {})
        if not record:
            return {"run": None}
        return {"run": {**format_run_record_times(record), "state": "running"}}

    @app.get("/runs/{run_id}/events")
    async def replay(run_id: str, after: int = 0, tail: int = 0, compact: bool = True):
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        if not await asyncio.to_thread((project.runs / "controller" / run_id / "status.json").is_file):
            raise HTTPException(404, "Run not found")
        return StreamingResponse(
            run_events(
                run_id,
                after=max(0, after),
                tail_lines=max(0, min(tail, 50_000)),
                compact=compact,
            ),
            media_type="text/event-stream",
        )

    @app.get("/runs/{run_id}")
    async def run_status(run_id: str):
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        record = await read_json(project.runs / "controller" / run_id / "status.json")
        if record is None:
            raise HTTPException(404, "Run not found")
        return {
            **format_run_record_times(record),
            "state": {"PASS": "completed", "FAILED": "failed"}.get(record["status"], "running"),
        }

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        raise HTTPException(409, "Executor cancellation requires adapter confirmation; continue observing this run.")

    @app.post("/api/workflows")
    async def workflow(body: WorkflowRequest):
        if tasks:
            raise HTTPException(409, "A workflow is already active.")
        run_id = uuid.uuid4().hex
        directory = project.runs / "controller" / run_id
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        await write_json(directory / "status.json", {
            "run_id": run_id,
            "status": "RUNNING",
            "mode": "workflow",
            "change_id": body.change_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "log": str(directory / "output.log"),
        })
        state["active_run_id"] = run_id
        task = asyncio.create_task(
            service.execute(
                mode="workflow",
                change=body.change_id,
                workflow_timeout_minutes=body.workflow_timeout_minutes,
                supervisor_timeout_minutes=body.supervisor_timeout_minutes,
                max_attempts=body.max_attempts,
                run_id=run_id,
            )
        )
        tasks.add(task)
        task.add_done_callback(completed)
        return {"status": "STARTING", "change_id": body.change_id, "run_id": run_id}

    @app.post("/api/shutdown")
    async def stop():
        if tasks:
            raise HTTPException(409, "A workflow is active; keep observing it.")
        if shutdown is not None:
            shutdown()
        return {"status": "STOPPING"}

    return app


async def serve(project: Project, port: int, *, browser: bool = False) -> None:
    """Держит панель в этом процессе до Ctrl+C или POST /api/shutdown."""
    import webbrowser

    state = await read_json(project.panel_state)
    if not state or state.get("project_root") != str(project.root):
        raise OpsxError("PANEL_STATE_INVALID", "Start the panel through opsx start.")
    holder: dict = {}
    app = create_app(project, state, shutdown=lambda: setattr(holder["server"], "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    holder["server"] = server
    url = str(state.get("url") or f"http://127.0.0.1:{port}")
    print(url, flush=True)
    if browser:
        await asyncio.to_thread(webbrowser.open, url)
    try:
        await server.serve()
    finally:
        await asyncio.to_thread(project.panel_state.unlink, missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(serve(Project.at(args.project), args.port, browser=args.browser))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
