#!/usr/bin/env python3
"""Local owner UI for a named Bug Team repair.

Запуск из корня репозитория (Windows/Linux):

    poetry run python -u opsx/opsx_runtime/ui/named_bug_repair/server.py

Откройте в браузере: http://127.0.0.1:8765/

POST ``/repair`` отдаёт ``text/event-stream``: строки stdout/stderr hop в реальном
времени (те же, что в терминале сервера). Финальное событие ``event: done``.
"""

from __future__ import annotations

from opsx.project_context import project_root

import argparse
import asyncio
import json
import queue
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_UI_DIR = Path(__file__).resolve().parent
RUNTIME_ROOT = _UI_DIR.parents[1]
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

from bootstrap import prepare_named_bug_repair_runtime  # noqa: E402
from cursor_bridge import owner_named_cursor_bridge  # noqa: E402
from intake import run_owner_named_bug_repair  # noqa: E402
from stream_log import emit_console_line, format_exception_for_log  # noqa: E402
from workflows.stop_errors import OpsxStageStop, format_stage_stop  # noqa: E402

_DEFAULT_PORT = 8765
_STREAM_SENTINEL = object()
_CLIENT_DISCONNECTED = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)


def _is_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, _CLIENT_DISCONNECTED):
        return True
    winerror = getattr(exc, "winerror", None)
    return isinstance(exc, OSError) and winerror in (10053, 10054, 10058)


def _utc_now() -> str:
    """Текущий UTC timestamp для wire/replay UI."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "error"})
_MAX_RETAINED_RUNS = 20


def _parse_form_body(raw: bytes, content_type: str) -> dict[str, str]:
    """Parse urlencoded or the simple multipart form emitted by this UI."""

    if "multipart/form-data" in content_type:
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip().strip('"')
                break
        if not boundary:
            raise ValueError("multipart boundary missing")
        text = raw.decode("utf-8", errors="replace")
        fields: dict[str, str] = {}
        for chunk in text.split(f"--{boundary}"):
            if "Content-Disposition" not in chunk or 'name="' not in chunk:
                continue
            name_start = chunk.index('name="') + 6
            name_end = chunk.index('"', name_start)
            name = chunk[name_start:name_end]
            _, _, value_part = chunk.partition("\r\n\r\n")
            fields[name] = value_part.rsplit("\r\n", 1)[0]
        return fields
    parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def _sse_encode(
    *, event: str | None = None,
    data: str,
    event_id: int | None = None,
) -> bytes:
    """Encode one UTF-8 Server-Sent Event."""

    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    if event:
        parts.append(f"event: {event}")
    segments = data.split("\n") if data else [""]
    parts.extend(f"data: {segment}" for segment in segments)
    parts.append("")
    return ("\n".join(parts) + "\n").encode("utf-8")


@dataclass(frozen=True)
class RunEvent:
    sequence: int
    event: str
    data: dict[str, Any]


@dataclass
class RepairRun:
    run_id: str
    bug_text: str
    started_at: str = field(default_factory=_utc_now)
    state: str = "running"
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[RunEvent] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _condition: threading.Condition = field(init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _task: asyncio.Task[Any] | None = field(default=None, repr=False)
    _cancel_requested: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self._condition = threading.Condition(self._lock)

    @property
    def terminal(self) -> bool:
        with self._lock:
            return self.state in _TERMINAL_STATES

    def emit(self, event: str, data: dict[str, Any]) -> RunEvent:
        with self._condition:
            item = RunEvent(len(self.events) + 1, event, dict(data))
            self.events.append(item)
            self._condition.notify_all()
            return item

    def attach_task(
        self,
        loop: asyncio.AbstractEventLoop,
        task: asyncio.Task[Any],
    ) -> None:
        with self._lock:
            self._loop = loop
            self._task = task
            cancel_requested = self._cancel_requested
        if cancel_requested:
            loop.call_soon(task.cancel)

    def request_cancel(self) -> bool:
        with self._lock:
            if self.state in _TERMINAL_STATES:
                return False
            self._cancel_requested = True
            loop, task = self._loop, self._task
        self.emit(
            "cancelling",
            {
                "run_id": self.run_id,
                "message": "Останавливаем активную задачу…",
                "timestamp": _utc_now(),
            },
        )
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)
        return True

    def finish(
        self,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._condition:
            self.state = state
            self.result = result
            self.error = error
            self.finished_at = _utc_now()
            self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "state": self.state,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "result": self.result,
                "error": self.error,
                "last_event_id": len(self.events),
            }


class RepairRunRegistry:
    """One active writer plus a bounded replay history."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RepairRun] = {}
        self._active_run_id: str | None = None

    def create(self, bug_text: str) -> RepairRun:
        text = bug_text.strip()
        if not text:
            raise ValueError("Опишите баг перед запуском")
        if len(text) > 8000:
            raise ValueError("Описание бага не должно превышать 8000 символов")
        with self._lock:
            active = self.active()
            if active is not None:
                raise RuntimeError(active.run_id)
            run_id = f"repair-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
            record = RepairRun(run_id=run_id, bug_text=text)
            self._runs[run_id] = record
            self._active_run_id = run_id
            while len(self._runs) > _MAX_RETAINED_RUNS:
                oldest = next(iter(self._runs))
                if oldest == self._active_run_id:
                    break
                del self._runs[oldest]
            return record

    def get(self, run_id: str) -> RepairRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def active(self) -> RepairRun | None:
        with self._lock:
            if self._active_run_id is None:
                return None
            record = self._runs.get(self._active_run_id)
            if record is None or record.terminal:
                self._active_run_id = None
                return None
            return record

    def release(self, record: RepairRun) -> None:
        with self._lock:
            if self._active_run_id == record.run_id:
                self._active_run_id = None


class NamedBugRepairHandler(BaseHTTPRequestHandler):
    """Serve the UI, run repairs and replay their semantic event stream."""

    protocol_version = "HTTP/1.1"
    repo_root: Path = REPO_ROOT
    timeout_seconds: int = 2700
    registry: RepairRunRegistry = RepairRunRegistry()
    _sse_response_open: bool = False

    def handle(self) -> None:
        try:
            super().handle()
        except BaseException as exc:
            if _is_client_disconnect(exc):
                return
            raise

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            _ = self.wfile.write(body)
        except _CLIENT_DISCONNECTED:
            return

    def _begin_sse_response(self) -> None:
        """Открыть HTTP-ответ ``text/event-stream`` без ``Content-Length``."""
        self._sse_response_open = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        """Ответ JSON для REST-эндпоинтов UI."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            _ = self.wfile.write(body)
        except _CLIENT_DISCONNECTED:
            return

    def _write_sse(
        self,
        *,
        event: str | None = None,
        data: str = "",
        event_id: int | None = None,
    ) -> bool:
        """Записать SSE-событие. False — клиент отключился; repair в worker продолжается."""
        try:
            _ = self.wfile.write(
                _sse_encode(event=event, data=data, event_id=event_id),
            )
            self.wfile.flush()
            return True
        except _CLIENT_DISCONNECTED:
            return False
        except OSError as exc:
            if _is_client_disconnect(exc):
                return False
            raise

    def _emit_run_sse(
        self,
        record: RepairRun,
        *,
        event: str,
        data: dict[str, Any],
        client_connected: bool,
    ) -> bool:
        """Записать одно SSE-событие и сохранить его в replay-журнал ``record``."""
        item = record.emit(event, data)
        if not client_connected:
            return False
        payload = json.dumps(data, ensure_ascii=False)
        return self._write_sse(event=event, data=payload, event_id=item.sequence)

    def _stream_run(self, record: RepairRun, *, after: int = 0) -> None:
        """Повтор SSE с ``after`` (восстановление после перезагрузки страницы)."""
        self._begin_sse_response()
        client_connected = True
        with record._condition:
            pending = [item for item in record.events if item.sequence > after]
        for item in pending:
            if not client_connected:
                break
            payload = json.dumps(item.data, ensure_ascii=False)
            client_connected = self._write_sse(
                event=item.event,
                data=payload,
                event_id=item.sequence,
            )
        if client_connected and record.terminal:
            _ = self._write_sse(
                event="done",
                data=json.dumps({"state": record.state}, ensure_ascii=False),
            )
        self._sse_response_open = False
        self.close_connection = True

    def _stream_repair_sse(self, bug_text: str, *, change_id: str = "") -> None:
        """Запустить repair в фоне и стримить stage/log/result клиенту (SSE)."""
        record = self.registry.create(bug_text)
        event_queue: queue.Queue[Any] = queue.Queue()

        def on_log_line(line: str) -> None:
            event_queue.put(("log", line))

        def on_progress(payload: dict[str, Any]) -> None:
            event_queue.put(("progress", payload))

        async def _repair_with_bridge() -> dict[str, Any]:
            prepare_named_bug_repair_runtime(repo_root=self.repo_root)
            resolved_change = (change_id or "").strip() or "owner-named"
            async with owner_named_cursor_bridge(
                self.repo_root,
                change_id=resolved_change,
            ) as bridge:
                return await run_owner_named_bug_repair(
                    repository_path=self.repo_root,
                    bug_text=bug_text,
                    change_id=change_id or None,
                    timeout_seconds=self.timeout_seconds,
                    on_log_line=on_log_line,
                    on_progress=on_progress,
                    bridge_runtime=bridge,
                )

        result_holder: dict[str, Any] = {}
        error_holder: dict[str, str | None] = {"error": None}

        def worker() -> None:
            try:
                emit_console_line(
                    "named_bug_repair: repair worker started (POST /repair accepted)"
                )
                result_holder["result"] = asyncio.run(_repair_with_bridge())
            except OpsxStageStop as exc:
                text = format_stage_stop(exc)
                emit_console_line(text)
                error_holder["error"] = text
                event_queue.put(("log", text))
            except Exception as exc:
                text = format_exception_for_log(exc)
                emit_console_line(text)
                error_holder["error"] = text
                event_queue.put(("log", text))
            finally:
                emit_console_line("named_bug_repair: repair worker thread exiting")
                event_queue.put(_STREAM_SENTINEL)

        self._begin_sse_response()
        client_connected = self._emit_run_sse(
            record,
            event="run_started",
            data={
                "run_id": record.run_id,
                "started_at": record.started_at,
                "bug_text_preview": bug_text.strip()[:120],
            },
            client_connected=True,
        )
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        notified_disconnect = False
        while True:
            try:
                item = event_queue.get(timeout=20.0)
            except queue.Empty:
                if client_connected:
                    try:
                        _ = self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                    except _CLIENT_DISCONNECTED:
                        client_connected = False
                    except OSError as exc:
                        if _is_client_disconnect(exc):
                            client_connected = False
                        else:
                            raise
                if not client_connected and not notified_disconnect:
                    notified_disconnect = True
                    sys.stderr.write(
                        "named_bug_repair: SSE client disconnected; "
                        "repair hop continues in background\n"
                    )
                continue
            if item is _STREAM_SENTINEL:
                break
            kind, payload = item
            if kind == "log":
                client_connected = self._emit_run_sse(
                    record,
                    event="log",
                    data={"text": str(payload)},
                    client_connected=client_connected,
                )
            elif kind == "progress" and isinstance(payload, dict):
                event_name = str(payload.get("event") or "stage")
                if event_name == "result":
                    client_connected = self._emit_run_sse(
                        record,
                        event="result",
                        data=payload,
                        client_connected=client_connected,
                    )
                else:
                    client_connected = self._emit_run_sse(
                        record,
                        event="stage",
                        data=payload,
                        client_connected=client_connected,
                    )
        summary = (result_holder.get("result") or {}).get(
            "owner_named_check_repair_summary",
        )
        final_payload: dict[str, Any]
        if isinstance(summary, dict):
            final_payload = dict(summary)
        else:
            final_payload = {
                "status": (result_holder.get("result") or {}).get(
                    "owner_named_check_repair_status",
                    "UNKNOWN",
                ),
            }
        if error_holder["error"]:
            final_payload["error"] = error_holder["error"]
        record.finish(
            "failed" if final_payload.get("error") else "completed",
            result=final_payload,
            error=str(final_payload.get("error") or "") or None,
        )
        client_connected = self._emit_run_sse(
            record,
            event="result",
            data=final_payload,
            client_connected=client_connected,
        )
        if client_connected:
            _ = self._write_sse(
                event="done",
                data=json.dumps({"state": record.state}, ensure_ascii=False),
            )
        self._sse_response_open = False
        self.close_connection = True
        self.registry.release(record)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            html = (_UI_DIR / "index.html").read_bytes()
            self._send_bytes(HTTPStatus.OK, html, content_type="text/html; charset=utf-8")
            return
        if parsed.path == "/runs/active":
            active = self.registry.active()
            self._send_json(
                HTTPStatus.OK,
                {"run": active.snapshot() if active is not None else None},
            )
            return
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) in {2, 3} and parts[0] == "runs":
            record = self.registry.get(parts[1])
            if record is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            if len(parts) == 2:
                self._send_json(HTTPStatus.OK, record.snapshot())
                return
            if parts[2] == "events":
                after_raw = parse_qs(parsed.query).get("after", ["0"])[0]
                try:
                    after = int(after_raw)
                except ValueError:
                    after = 0
                try:
                    self._stream_run(record, after=after)
                except (BrokenPipeError, ConnectionResetError):
                    return
                return
        self._send_bytes(HTTPStatus.NOT_FOUND, b"not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
            record = self.registry.get(parts[1])
            if record is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "run not found"})
                return
            accepted = record.request_cancel()
            self._send_json(
                HTTPStatus.ACCEPTED if accepted else HTTPStatus.CONFLICT,
                {"accepted": accepted, "run": record.snapshot()},
            )
            return
        if parsed.path != "/repair":
            self._send_bytes(HTTPStatus.NOT_FOUND, b"not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            content_type = self.headers.get("Content-Type", "")
            fields = _parse_form_body(raw, content_type)
            bug_text = fields.get("bug", "")
            change_id = fields.get("change", "")
            self._stream_repair_sse(bug_text, change_id=change_id)
        except ValueError as exc:
            if not self._sse_response_open:
                self._send_bytes(HTTPStatus.BAD_REQUEST, str(exc).encode("utf-8"))
        except BaseException as exc:
            if _is_client_disconnect(exc) or self._sse_response_open:
                return
            message = f"{type(exc).__name__}: {exc}"
            try:
                self._send_bytes(HTTPStatus.INTERNAL_SERVER_ERROR, message.encode("utf-8"))
            except BaseException as send_exc:
                if _is_client_disconnect(send_exc):
                    return
                raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Owner-named bug repair HTML UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--timeout", type=int, default=2700)
    args = parser.parse_args()

    prepare_named_bug_repair_runtime(repo_root=args.repo.resolve())

    handler = type(
        "ConfiguredNamedBugRepairHandler",
        (NamedBugRepairHandler,),
        {
            "repo_root": args.repo.resolve(),
            "timeout_seconds": args.timeout,
            "registry": RepairRunRegistry(),
        },
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.allow_reuse_address = False
    print(
        f"Named bug repair UI: http://{args.host}:{args.port}/\n"
        f"Repo: {args.repo.resolve()}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
