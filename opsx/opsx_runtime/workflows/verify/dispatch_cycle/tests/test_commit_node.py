"""Контракт ``run_verify_finding_commit``: outcome и ``finding_commit_result``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workflows.verify.dispatch_cycle.commit_node import run_verify_finding_commit


@pytest.mark.asyncio
async def test_commit_records_finding_commit_result(tmp_path) -> None:
    (tmp_path / "opsx").mkdir()
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "visual",
        "dispatcher_current_item": {
            "finding_id": "F-0001",
            "observation": "pytest timeout",
        },
        "verify_finding_fix": {
            "status": "FIXED",
            "files_changed": ["tests/unit/test_foo.py"],
            "summary": "fix mocks",
        },
    }
    commit_patch = {
        "commit_status": "COMMITTED",
        "commit_sha": "abc123",
        "commit_files": ["tests/unit/test_foo.py"],
        "commit_subject": "fix(tests): foo",
        "commit_body": "details",
        "error": None,
    }
    with patch(
        "workflows.verify.dispatch_cycle.commit_node.CommitAgent",
    ) as agent_cls:
        agent_cls.return_value.run = AsyncMock(return_value=commit_patch)
        result = await run_verify_finding_commit(state, repair_adapter=MagicMock())

    assert result["commit_status"] == "COMMITTED"
    assert result["finding_commit_result"]["status"] == "COMMITTED"
    assert result["finding_commit_result"]["finding_id"] == "F-0001"
    outcome = result["verify_finding_commit_outcome"]
    assert outcome["status"] == "COMMITTED"
    assert outcome["commit_sha"] == "abc123"
    assert result.get("verify_workflow_code_changed") is True


@pytest.mark.asyncio
async def test_commit_skips_duplicate_revision(tmp_path) -> None:
    (tmp_path / "opsx").mkdir()
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "c1",
        "dispatcher_current_item": {"finding_id": "F-1"},
        "verify_finding_fix": {
            "status": "FIXED",
            "files_changed": ["tests/unit/test_foo.py"],
        },
        "finding_commit_revision_by_finding": {
            "F-1": "deadbeef",
        },
    }
    with patch(
        "workflows.verify.dispatch_cycle.commit_node.repair_files_revision_key",
        return_value="deadbeef",
    ):
        result = await run_verify_finding_commit(state, repair_adapter=MagicMock())
    assert result["commit_status"] == "SKIPPED_ALREADY_COMMITTED"
