"""Unit tests for repair hop debug artifact persistence."""

from __future__ import annotations

from agents.repair.execute_role import _persist_hop_artifacts, make_hop
from model_config.agent_roles import AgentRole
from workflows.repair.schemas import RepairState


def test_persist_hop_artifacts_writes_prompt_context_and_raw(tmp_path):
    run_dir = tmp_path / "mission"
    run_dir.mkdir()
    state = RepairState(
        mission_id="m1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(run_dir),
    )
    meta = make_hop(state, AgentRole.CLASSIFIER)
    artifacts = _persist_hop_artifacts(
        state,
        meta.hop,
        prompt="TASK\n\nCONTEXT\n",
        context={"finding": {"id": "F-1"}},
        meta=meta,
        raw_text='{"status":"PASS"}',
    )
    workers = run_dir / "workers"
    assert (workers / f"{meta.hop}.prompt.txt").read_text(
        encoding="utf-8"
    ) == "TASK\n\nCONTEXT\n"
    assert (workers / f"{meta.hop}.context.json").exists()
    raw_path = workers / f"{meta.hop}.response.raw.txt"
    assert raw_path.read_text(encoding="utf-8") == '{"status":"PASS"}'
    assert artifacts.prompt_artifact == f"workers/{meta.hop}.prompt.txt"
    assert artifacts.response_raw_artifact == f"workers/{meta.hop}.response.raw.txt"
    assert len(artifacts.prompt_sha256) == 64
    assert len(artifacts.response_raw_sha256 or "") == 64
