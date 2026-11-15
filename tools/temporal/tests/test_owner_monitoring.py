"""Tests for owner monitoring manifest paths and payload shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from owner_monitoring import (
    SCHEMA_VERSION,
    build_monitoring_manifest,
    latest_manifest_path,
    write_owner_monitoring_manifest,
    workflow_manifest_path,
)


def test_build_monitoring_manifest_includes_poll_command(tmp_path: Path):
    repo = tmp_path
    payload = build_monitoring_manifest(
        repo=repo,
        pipeline="project-bug-hunt",
        phase="WORKFLOW_STARTED",
        resolved={"base": "stage", "head": "feature/x", "change": "my-change"},
        review="low",
        repair="low",
        workflow_id="project-bug-hunt-poc-real-my-change",
        temporal_ui="http://localhost:8233/namespaces/default/workflows/wf",
    )
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "status_poll_command" in payload
    assert "runtime.py" in payload["status_poll_command"]
    assert "status --workflow-id" in payload["status_poll_command"]
    assert payload["temporal_ui_owner_note"]


def test_write_owner_monitoring_manifest_creates_latest_and_per_workflow(tmp_path: Path):
    repo = tmp_path
    wf = "project-bug-hunt-poc-real-demo"
    body = build_monitoring_manifest(
        repo=repo,
        pipeline="project-bug-hunt",
        phase="COMPLETE",
        resolved={"change": "demo"},
        review="low",
        repair="low",
        workflow_id=wf,
        operator_status="PASS",
    )
    dest = write_owner_monitoring_manifest(repo, body, workflow_id=wf)
    assert dest == latest_manifest_path(repo)
    assert dest.is_file()
    assert workflow_manifest_path(repo, wf).is_file()
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded["workflow_id"] == wf
