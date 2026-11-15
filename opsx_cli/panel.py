"""Async local HTTP frontend over the same application services as the CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import secrets
import uuid
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from .context import OpsxError, Project
from .service import RunService
from .storage import read_json


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    timeout_seconds: float = Field(default=2700, gt=0)


def create_app(project: Project, state: dict, *, shutdown=None) -> FastAPI:
    """Compose one project frontend with explicit state and shutdown ownership."""
    service = RunService(project)
    tasks: set[asyncio.Task] = set()
    app = FastAPI(title="OPSX", docs_url=None, redoc_url=None, openapi_url=None)

    def completed(done):
        tasks.discard(done)
        if not done.cancelled():
            done.exception()  # Durable run status owns the outcome.

    async def run_events(run_id: str, task=None, after: int = 0):
        """Replay complete log lines and then observe the same durable run."""
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        directory = project.runs / "controller" / run_id
        offset, sequence, pending = 0, 0, b""
        yield "event: run_started\ndata: " + json.dumps({"run_id": run_id}) + "\n\n"
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
                line = raw.decode("utf-8", "replace")
                event, payload = "log", {"text": line}
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict) and isinstance(parsed.get("opsx_event"), dict):
                        payload = parsed["opsx_event"]
                        event = str(payload.get("event") or "stage")
                except ValueError:
                    payload = {"text": line}
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
                        record = {"status": "FAILED", "error": str(exc)}
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
        return {"runs": await service.history()}

    @app.get("/runs/active")
    async def active():
        records = await service.history()
        record = next((r for r in records if r.get("status") in {"RUNNING", "OUTCOME_UNKNOWN"}), None)
        if record is not None:
            record = {**record, "state": "running"}
        return {"run": record}

    @app.get("/runs/{run_id}/events")
    async def replay(run_id: str, after: int = 0):
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        if not await asyncio.to_thread((project.runs / "controller" / run_id / "status.json").is_file):
            raise HTTPException(404, "Run not found")
        return StreamingResponse(run_events(run_id, after=max(0, after)), media_type="text/event-stream")

    @app.get("/runs/{run_id}")
    async def run_status(run_id: str):
        if not run_id.isalnum():
            raise HTTPException(400, "Invalid run id")
        record = await read_json(project.runs / "controller" / run_id / "status.json")
        if record is None:
            raise HTTPException(404, "Run not found")
        return {**record, "state": {"PASS": "completed", "FAILED": "failed"}.get(record["status"], "running")}

    @app.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        raise HTTPException(409, "Executor cancellation requires adapter confirmation; continue observing this run.")

    @app.post("/api/workflows")
    async def workflow(body: WorkflowRequest):
        if tasks:
            raise HTTPException(409, "A workflow is already active.")
        task = asyncio.create_task(service.execute(mode="workflow", change=body.change_id,
                                                   timeout=body.timeout_seconds))
        tasks.add(task)
        task.add_done_callback(completed)
        return {"status": "STARTING", "change_id": body.change_id}

    @app.get("/repair", response_class=HTMLResponse)
    async def repair_ui():
        path = project.core / "opsx/opsx_runtime/ui/named_bug_repair/index.html"
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    @app.post("/repair")
    async def repair(request: Request):
        # Existing repair UI posts URL-encoded data; preserve that interface.
        if tasks:
            raise HTTPException(409, "A workflow is already active.")
        raw = await request.body()
        if len(raw) > 65536:
            raise HTTPException(413, "Repair request too large.")
        if "application/json" in request.headers.get("content-type", ""):
            body = json.loads(raw)
            bug, change = str(body.get("bug", "")), str(body.get("change", "owner-named"))
        else:
            fields = parse_qs(raw.decode("utf-8"))
            bug, change = fields.get("bug", [""])[0], fields.get("change", ["owner-named"])[0]
        if not bug.strip():
            raise HTTPException(400, "Describe the defect.")
        run_id = uuid.uuid4().hex
        task = asyncio.create_task(service.execute(mode="repair", change=change or "owner-named", bug=bug, run_id=run_id))
        tasks.add(task)
        task.add_done_callback(completed)
        return StreamingResponse(run_events(run_id, task=task), media_type="text/event-stream")

    @app.post("/api/shutdown")
    async def stop():
        if tasks:
            raise HTTPException(409, "A workflow is active; keep observing it.")
        if shutdown is not None:
            shutdown()
        return {"status": "STOPPING"}

    return app


async def serve(project: Project, port: int) -> None:
    state = await read_json(project.panel_state)
    if not state or state.get("project_root") != str(project.root):
        raise OpsxError("PANEL_STATE_INVALID", "Start the panel through opsx start.")
    holder: dict = {}
    app = create_app(project, state, shutdown=lambda: setattr(holder["server"], "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    holder["server"] = server
    await server.serve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(Project.at(args.project), args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
