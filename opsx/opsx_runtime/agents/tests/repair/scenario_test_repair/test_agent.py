"""SDK-boundary tests using the stored, actual ScenarioCoverage FAIL context.

SDK replies and filesystem deltas are controlled in these tests; no live
model is started and no checked-out source file is modified.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel

from agents.adapters.abc_adapter import AgentResult
from agents.common.scenario_files import scenario_test_relpath
from agents.repair.scenario_test_repair import agent as agent_module
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.repair.scenario_test_repair.schemas import ScenarioTestRepairAnswer
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)


class FakeAdapter:
    def __init__(self, parsed: BaseModel | None, *, error: Exception | None = None):
        self.parsed = parsed
        self.error = error
        self.calls: list[dict] = []

    async def run(self, state, **kwargs):
        self.calls.append({"state": state, **kwargs})
        if self.error:
            raise self.error
        return AgentResult(
            text="adapter structured output", status="COMPLETED",
            agent_id="fake-sdk-id", run_id="fake-sdk-run-id",
            parsed=self.parsed,
        )


def _good_answer(audit: dict, paths: list[str]) -> ScenarioTestRepairAnswer:
    ids = [x["scenario_id"] for x in audit["gaps"]]
    return ScenarioTestRepairAnswer(
        status="CANDIDATE_PENDING_VERIFY",
        summary="Test-only candidate; independent review not yet run.",
        addressed_gap_ids=ids,
        files_changed=paths,
        checks_reported=[],
        recovery_contract="Independent ScenarioCoverageVerify required.",
    )


def _snapshot_pair(repo_root, change_id: str, scenario_id: str):
    """Content hashes anchored to mapped canonical test path."""
    path = scenario_test_relpath(change_id, scenario_id)
    original = (repo_root / path).read_bytes()
    before = {path: hashlib.sha256(original).hexdigest()}
    # No on-disk write: only the test adapter simulates a changed test file.
    after = {path: hashlib.sha256(original + b"\n# controlled SDK-delta sentinel\n").hexdigest()}
    return path, before, after


@pytest.mark.asyncio
async def test_agent_receives_current_apply_context_verbatim_and_never_claims_pass(
    monkeypatch, audit_context, repo_root,
):
    gaps = audit_context["scenario_coverage_answer"]["gaps"]
    paths = [
        scenario_test_relpath(audit_context["change_id"], g["scenario_id"]) for g in gaps
    ]
    sid = gaps[0]["scenario_id"]
    path, before, after = _snapshot_pair(repo_root, audit_context["change_id"], sid)
    for p in paths:
        rel = repo_root / p
        if rel.is_file():
            before[p] = hashlib.sha256(rel.read_bytes()).hexdigest()
            after[p] = hashlib.sha256(rel.read_bytes() + b"\n# sentinel\n").hexdigest()
    snapshots = iter((before, after))
    monkeypatch.setattr(agent_module, "workspace_snapshot", lambda _root: next(snapshots))
    adapter = FakeAdapter(_good_answer(audit_context["scenario_coverage_answer"], paths))
    agent = ScenarioTestRepairAgent(adapter)

    patch = await agent.run(audit_context, repository_path=str(repo_root))
    assert patch["scenario_test_repair_status"] == "CANDIDATE_PENDING_VERIFY"
    assert patch["scenario_test_repair_independent_verified"] is False
    assert set(patch["scenario_test_repair_changed_paths"]) == set(paths)
    assert len(adapter.calls) == 1

    sent = adapter.calls[0]
    assert sent["resume_agent_id"] is None
    assert sent["payload_model"] is ScenarioTestRepairAnswer
    assert sent["state"]["repository_path"] == str(repo_root)
    assert sent["state"]["workspace"] == str(repo_root)
    assert "edit" in sent["tools"]
    assert "shell" not in sent["tools"]  # SDK create/send tool capability, not a prompt hint

    prompt = sent["prompt"]
    exact_audit = json.dumps(
        audit_context["scenario_coverage_answer"], ensure_ascii=False, indent=2,
    )
    exact_impl = json.dumps(audit_context["answer"], ensure_ascii=False, indent=2)
    exact_artifacts = json.dumps(
        load_change_artifact_bundle(audit_context["change_id"], repo_root=repo_root),
        ensure_ascii=False, indent=2,
    )
    exact_machine = json.dumps(
        collect_apply_machine_context(audit_context["change_id"], repo_root=repo_root),
        ensure_ascii=False, indent=2,
    )
    # The live Apply input assembler, not handwritten scenario examples.
    assert exact_audit in prompt
    assert exact_impl in prompt
    assert exact_artifacts in prompt
    assert exact_machine in prompt
    assert str(repo_root) in prompt
    assert "Only tests/**" in prompt
    assert "FROZEN REPAIR BINDINGS" in prompt
    assert "REPAIR PLAYBOOK" in prompt
    assert "process_pending" in prompt


@pytest.mark.asyncio
async def test_output_is_revalidated_through_pydantic_and_invalid_output_pauses(
    monkeypatch, audit_context, repo_root,
):
    class InvalidParsed(BaseModel):
        status: str = "APPLY_COMPLETE"
        summary: str = "not the repair schema"

    sid = audit_context["scenario_coverage_answer"]["gaps"][0]["scenario_id"]
    _path, before, after = _snapshot_pair(repo_root, audit_context["change_id"], sid)
    snapshots = iter((before, after))
    monkeypatch.setattr(agent_module, "workspace_snapshot", lambda _root: next(snapshots))
    adapter = FakeAdapter(InvalidParsed())
    patch = await ScenarioTestRepairAgent(adapter).run(audit_context)
    assert patch["scenario_test_repair_status"] == "BLOCKED"
    assert "ValidationError" in patch["scenario_test_repair_owner_attention"]
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_sdk_failure_still_checks_changed_workspace_and_blocks(
    monkeypatch, audit_context, repo_root,
):
    sid = audit_context["scenario_coverage_answer"]["gaps"][0]["scenario_id"]
    _path, before, after = _snapshot_pair(repo_root, audit_context["change_id"], sid)
    snapshots = iter((before, after))
    monkeypatch.setattr(agent_module, "workspace_snapshot", lambda _root: next(snapshots))
    adapter = FakeAdapter(None, error=RuntimeError("Cursor stream lost after write"))
    patch = await ScenarioTestRepairAgent(adapter).run(audit_context)
    assert patch["scenario_test_repair_status"] == "BLOCKED"
    assert "REPAIR_EXECUTION_UNPROVEN" in patch["scenario_test_repair_owner_attention"]


@pytest.mark.asyncio
async def test_unexpected_production_mutation_is_blocked_even_on_valid_output(
    monkeypatch, audit_context, repo_root,
):
    gaps = audit_context["scenario_coverage_answer"]["gaps"]
    paths = [
        scenario_test_relpath(audit_context["change_id"], g["scenario_id"]) for g in gaps
    ]
    sid = gaps[0]["scenario_id"]
    _path, before, after = _snapshot_pair(repo_root, audit_context["change_id"], sid)
    for p in paths:
        rel = repo_root / p
        if rel.is_file():
            before[p] = hashlib.sha256(rel.read_bytes()).hexdigest()
            after[p] = hashlib.sha256(rel.read_bytes() + b"\n# sentinel\n").hexdigest()
    after = {**after, "src/stroy/notifications/forbidden.py": "unexpected hash"}
    snapshots = iter((before, after))
    monkeypatch.setattr(agent_module, "workspace_snapshot", lambda _root: next(snapshots))
    adapter = FakeAdapter(_good_answer(audit_context["scenario_coverage_answer"], paths))
    patch = await ScenarioTestRepairAgent(adapter).run(audit_context)
    assert patch["scenario_test_repair_status"] == "BLOCKED"
    assert "REPAIR_SCOPE_VIOLATION" in patch["scenario_test_repair_owner_attention"]


@pytest.mark.asyncio
async def test_stale_historical_audit_never_reaches_sdk(
    audit_context, repo_root,
):
    old = {
        **audit_context,
        "scenario_coverage_source_snapshot": {"source_digest": "historical-stale-digest"},
    }
    adapter = FakeAdapter(None)
    patch = await ScenarioTestRepairAgent(adapter).run(old, repository_path=str(repo_root))
    assert patch["scenario_test_repair_status"] == "BLOCKED"
    assert "REPAIR_SOURCE_STALE" in patch["scenario_test_repair_owner_attention"]
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_explicit_path_cannot_replay_other_repository_audit(
    audit_context, repo_root, tmp_path,
):
    other = tmp_path / "not-the-audited-repo"
    other.mkdir()
    adapter = FakeAdapter(None)
    # Supply the VALID repo as explicit target, with a different source
    # repository in the incoming state: identity replay must be rejected.
    stale = {**audit_context, "repository_path": str(other)}
    patch = await ScenarioTestRepairAgent(adapter).run(
        stale, repository_path=str(repo_root),
    )
    assert patch["scenario_test_repair_status"] == "BLOCKED"
    assert "REPAIR_REPOSITORY_IDENTITY_MISMATCH" in patch["scenario_test_repair_owner_attention"]
    assert adapter.calls == []


def test_gap_ids_accepts_multiple_facets_for_one_scenario_row() -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    assert agent_module._gap_ids({
        "gaps": [
            {"scenario_id": sid, "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST"},
            {"scenario_id": sid, "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST"},
        ],
    }) == [sid]
