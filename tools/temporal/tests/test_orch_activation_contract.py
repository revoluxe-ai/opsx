"""Activation contract for /opsx-orch-bugbot-converge and /opsx-orch-bug-fix."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMANDS = ("opsx-orch-bugbot-converge", "opsx-orch-bug-fix")
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


def test_orch_commands_are_functional_proven() -> None:
    manifest = (ROOT / "opsx/registry/command_transfer_manifest.md").read_text(encoding="utf-8")
    for command in COMMANDS:
        assert f"| `{command}` | `FUNCTIONAL_PROVEN` |" in manifest
        body = (ROOT / ".cursor/commands" / f"{command}.md").read_text(encoding="utf-8")
        assert "SOURCE_STAGED_MIGRATION_HOLD" not in body
        assert "FUNCTIONAL_PROVEN" in body
        assert "WRAPPER_PRODUCT_WRITE: NO" in body
        assert "GIT_COMMIT: FORBIDDEN" in body
        assert "/opsx-bug-team" in body
        assert "OPSX-WB-ORCH-" in body


def test_converge_delegates_to_temporal_runtime() -> None:
    body = (ROOT / ".cursor/commands/opsx-orch-bugbot-converge.md").read_text(encoding="utf-8")
    assert "python tools/temporal/runtime.py run bugbot" in body
    assert "python tools/temporal/orch_target.py converge-plan" in body
    fix = (ROOT / ".cursor/commands/opsx-orch-bug-fix.md").read_text(encoding="utf-8")
    assert "START_TEMPORAL: FORBIDDEN" in fix
    assert "python tools/temporal/runtime.py run" not in fix
    assert "TASK_BACKGROUND" not in fix
    assert "vue-npm" not in body
    assert "vue-npm" not in fix


def test_orch_activation_rows_bind_current_bytes() -> None:
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
            "opsx-lite",
            "project-generic",
            "temporal-cursor-sdk-runtime-v1",
        ]


def test_orch_activation_provenance_is_unique() -> None:
    path = ROOT / "opsx/registry/provenance.ndjson"
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching = [
        event
        for event in events
        if event.get("event") in {"orch_temporal_target_activation", "target_orchestration_activation"}
        and set(event.get("commands") or []) == set(COMMANDS)
    ]
    assert matching
    assert any(event.get("integration_status") == "FULL" for event in matching)
    assert any(event.get("source_commit") == "91311ada29721cfb9aab7a00e1dc4d2a0a8d1982" for event in matching)
