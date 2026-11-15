"""Target activation contract for the four /tempo-* commands."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ("tempo-bugbot", "tempo-project-bug-hunt", "tempo-security", "tempo-verification")
def _post_transfer_source_hashes() -> dict[str, str]:
    transfer = json.loads(
        (ROOT / "opsx/transfer/TRANSFER_MANIFEST.json").read_text(encoding="utf-8-sig")
    )
    return {row["command"]: row["source_sha256"] for row in transfer["commands"]}



def _git_blob_sha256(relative_path: str, revision: str = "HEAD") -> str:
    blob = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{revision}:{relative_path}"]
    )
    return hashlib.sha256(blob).hexdigest()


def _activation_rows() -> list[dict]:
    path = ROOT / "opsx/registry/command_activation.ndjson"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def test_temporal_commands_are_functional_proven() -> None:
    manifest = (ROOT / "opsx/registry/command_transfer_manifest.md").read_text(encoding="utf-8")
    for command in COMMANDS:
        assert f"| `{command}` | `FUNCTIONAL_PROVEN` |" in manifest
        body = (ROOT / ".cursor/commands" / f"{command}.md").read_text(encoding="utf-8")
        assert "SOURCE_STAGED_MIGRATION_HOLD" not in body
        assert "python tools/temporal/runtime.py run" in body
def test_temporal_activation_rows_bind_current_bytes() -> None:
    rows = _activation_rows()
    for command in COMMANDS:
        matching = [row for row in rows if row.get("command_id") == command]
        assert len(matching) == 1
        row = matching[0]
        path = ROOT / str(row["target_path"])
        assert row["installed"] is True
        assert row["runtime_class"] == "ACTIVE"
        assert row["source_file_hash"] == _post_transfer_source_hashes()[command]
        relative_path = path.relative_to(ROOT).as_posix()
        assert row["identity_mode"] == "git_blob_sha256_v1"
        assert row["command_hash"] == _git_blob_sha256(relative_path)
        assert row["adapters"] == [
            "project-generic",
            "temporal-cursor-sdk-runtime-v1",
            "cursor-executor-0.1.0",
        ]

def test_sdk_runtime_is_exact_and_isolated() -> None:
    temporal_req = (ROOT / "tools/temporal/requirements.txt").read_text(encoding="utf-8")
    executor_req = (ROOT / "tools/cursor_executor/requirements.txt").read_text(encoding="utf-8")
    executor_project = (ROOT / "tools/cursor_executor/pyproject.toml").read_text(encoding="utf-8")
    runtime = (ROOT / "tools/temporal/runtime.py").read_text(encoding="utf-8")
    assert "temporalio==1.33.0" in temporal_req
    assert "cursor-sdk==1.0.31" in executor_req
    assert 'requires-python = ">=3.12"' in executor_project
    assert "EXPECTED_PYTHON = (3, 12)" in runtime
    assert "m.version('cursor-sdk')=='1.0.31'" in runtime.replace(" ", "")
    root_project = ROOT / "pyproject.toml"
    if root_project.exists():
        assert "tools/temporal" not in root_project.read_text(encoding="utf-8").lower()

def test_temporal_activation_provenance_is_unique() -> None:
    path = ROOT / "opsx/registry/provenance.ndjson"
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching = [event for event in events if event.get("event") == "temporal_sdk_activation"]
    assert len(matching) == 1
    event = matching[0]
    assert event["commands"] == list(COMMANDS)
    assert event["integration_status"] == "FULL"
    assert event["runtime"] == {
        "python": "3.12",
        "cursor_sdk": "1.0.31",
        "cursor_executor": "0.1.0",
        "temporalio": "1.33.0",
    }
