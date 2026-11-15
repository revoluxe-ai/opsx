"""A9–A10: operator describe_workflow enrichment shape (hermetic)."""

from __future__ import annotations

import json
from pathlib import Path

from tempo_operator import (
    build_live_trace_section,
    build_pending_activity_section,
)


def test_pending_activity_includes_stc_hb_for_hunt_a9() -> None:
    pending = build_pending_activity_section("execute_project_bug_hunt")
    assert pending is not None
    assert pending["start_to_close_seconds"] == 3600
    assert pending["heartbeat_timeout_seconds"] == 900

    orch = build_pending_activity_section("execute_project_bug_hunt_orch")
    assert orch is not None
    assert orch["start_to_close_seconds"] == 3600
    assert orch["heartbeat_timeout_seconds"] == 900


def test_pending_activity_includes_stc_hb_for_bootstrap_a9() -> None:
    pending = build_pending_activity_section(
        "ensure_cursor_session",
        temporal_pending=type("P", (), {"attempt": 2, "last_failure": "heartbeat timeout"})(),
    )
    assert pending is not None
    assert pending["activity"] == "ensure_cursor_session"
    assert pending["start_to_close_seconds"] == 1800
    assert pending["heartbeat_timeout_seconds"] == 120
    assert pending["attempt"] == 2
    assert "last_failure" in pending


def test_live_trace_no_sdk_event_stream_a10(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()
    execution_id = "wf-test:epoch:agent-bootstrap"
    rec_dir = ledger_root / execution_id
    rec_dir.mkdir()
    (rec_dir / "record.json").write_text(
        json.dumps(
            {
                "execution_id": execution_id,
                "request_digest": "d",
                "workspace": ".",
                "lifecycle_phase": "RUNNING",
                "adapter_terminal": None,
                "agent_id": None,
                "run_id": None,
                "requested_route": {},
                "validated_route": None,
                "observed_route": None,
                "created_at": "t0",
                "updated_at": "t1",
            }
        ),
        encoding="utf-8",
    )
    hb = {"phase": "bootstrap", "workflow_id": "wf-test", "step": "ensure_cursor_session"}
    live = build_live_trace_section(
        "wf-test",
        ledger_root=ledger_root,
        last_heartbeat_details=hb,
    )
    assert live["last_heartbeat_details"] == hb
    assert live["ledger"]["lifecycle_phase"] == "RUNNING"
    assert "sdk_event_stream" not in json.dumps(live)
