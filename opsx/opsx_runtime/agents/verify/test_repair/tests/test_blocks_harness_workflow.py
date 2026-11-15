"""test_repair не авторизован на WF_PYTEST_COLLECTION."""

import pytest

from agents.verify.test_repair.agent import TestRepairAgent


class _StubAdapter:
    async def run(self, **kwargs: object) -> object:
        raise AssertionError("adapter should not run for blocked harness finding")


@pytest.mark.asyncio
async def test_blocks_wf_pytest_collection() -> None:
    agent = TestRepairAgent(_StubAdapter())
    patch = await agent.run(
        {
            "repository_path": "/tmp",
            "dispatcher_current_item": {
                "finding_id": "F-0001",
                "group_finding_ids": ["F-0001"],
            },
            "test_triage_result": {"classification": "TEST"},
            "analysis_result": {
                "analysis_identity": {"identity_status": "PARTIAL"},
                "input_integrity": {"status": "COMPLETE", "issues": []},
                "triage_summary": {"findings_total": 1},
                "findings": [
                    {
                        "finding_id": "F-0001",
                        "source_tool": "pytest",
                        "repair_workflow": "WF_PYTEST_COLLECTION",
                        "observation": "pytest: errors during collection tests/foo.py",
                        "classification": "FACT",
                        "evidence_refs": [],
                    },
                ],
            },
        },
    )
    assert patch["test_repair_execution_status"] == "BLOCKED"
    assert patch["verify_finding_fix"] is None
