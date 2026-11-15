from __future__ import annotations

import asyncio
import http.client
import json
from pathlib import Path
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server as server_module
from server import NamedBugRepairHandler, RepairRun, RepairRunRegistry, _sse_encode
from workflows.repair.progress import emit_repair_progress, observe_repair_progress


def test_sse_event_has_id_type_and_json_payload() -> None:
    payload = json.dumps({"stage": "RED_TEST_WORKER", "detail": "строка 1\nстрока 2"})

    encoded = _sse_encode(event="stage", data=payload, event_id=7).decode()

    assert encoded.startswith("id: 7\nevent: stage\ndata: ")
    assert encoded.endswith("\n\n")


def test_registry_allows_only_one_active_writer_and_retains_result() -> None:
    registry = RepairRunRegistry()
    first = registry.create("сломана доставка ответа")

    with pytest.raises(RuntimeError, match=first.run_id):
        registry.create("второй одновременный ремонт")

    first.emit("result", {"status": "FIXED"})
    first.finish("completed", result={"status": "FIXED"})
    registry.release(first)

    assert registry.active() is None
    assert registry.get(first.run_id).snapshot()["result"] == {"status": "FIXED"}
    assert registry.create("следующий ремонт").run_id != first.run_id


def test_cancel_request_cancels_attached_async_task() -> None:
    record = RepairRun(run_id="repair-test", bug_text="bug")

    async def run() -> bool:
        task = asyncio.current_task()
        assert task is not None
        record.attach_task(asyncio.get_running_loop(), task)
        assert record.request_cancel() is True
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(run()) is True
    assert record.events[-1].event == "cancelling"


def test_page_contains_progress_result_reconnect_and_cancel_controls() -> None:
    html = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

    assert 'id="timeline"' in html
    assert 'id="result"' in html
    assert 'id="cancel"' in html
    assert 'fetch("/runs/active")' in html
    assert "/events" in html


def test_progress_observer_receives_semantic_stage_event() -> None:
    events: list[dict[str, object]] = []

    with observe_repair_progress(events.append):
        emit_repair_progress(
            event="stage",
            stage="GREEN_VERIFIER",
            state="running",
            detail="Проверяем исправление",
        )

    assert events[0]["stage"] == "GREEN_VERIFIER"
    assert events[0]["state"] == "running"
    assert "timestamp" in events[0]


def test_http_repair_stream_contains_stages_result_and_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_repair(**kwargs):
        kwargs["on_progress"](
            {
                "event": "stage",
                "stage": "RED_TEST_WORKER",
                "state": "running",
                "detail": "Создаём RED-тест",
            }
        )
        kwargs["on_log_line"]("technical line\n")
        return {
            "owner_named_check_repair_summary": {
                "status": "COMPLETE_NO_CLASS_LESSON",
                "error": None,
                "files_changed": ["src/example.py"],
                "findings": [],
            }
        }

    class _BridgeCtx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(
        server_module,
        "owner_named_cursor_bridge",
        lambda *_a, **_k: _BridgeCtx(),
    )
    monkeypatch.setattr(server_module, "prepare_named_bug_repair_runtime", lambda **_k: None)
    monkeypatch.setattr(server_module, "run_owner_named_bug_repair", fake_repair)
    handler = type(
        "TestNamedBugHandler",
        (NamedBugRepairHandler,),
        {"repo_root": tmp_path, "registry": RepairRunRegistry()},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        body = urlencode({"bug": "broken reply"})
        connection.request(
            "POST",
            "/repair",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        wire = response.read().decode("utf-8")
        connection.close()

        assert response.status == 200
        assert "event: run_started" in wire
        assert "event: stage" in wire
        assert "RED_TEST_WORKER" in wire
        assert "event: result" in wire
        assert "src/example.py" in wire
        assert "event: done" in wire

        run_id = re.search(r'"run_id"\s*:\s*"(repair-[^"]+)"', wire).group(1)
        replay = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
        replay.request("GET", f"/runs/{run_id}/events")
        replay_response = replay.getresponse()
        replay_wire = replay_response.read().decode("utf-8")
        replay.close()

        assert replay_response.status == 200
        assert "event: run_started" in replay_wire
        assert "event: result" in replay_wire
        assert "event: done" in replay_wire
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
