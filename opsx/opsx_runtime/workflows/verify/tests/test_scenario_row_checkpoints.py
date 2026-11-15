"""One SDK agent per scenario: crash/restart, stale evidence and receipt safety."""
from __future__ import annotations

import asyncio
import json

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.verify.scenario_coverage_verify import row_receipts, row_single
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_observation, read_supported_row, row_check, save_supported_row,
    write_observation,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
    ScenarioCoverageVerifyModelAnswer,
    bind_scenario_coverage_authority,
)

_PLAN = "frozen-plan-v1"
_SOURCE = "f" * 64


def _workspace(root, count):
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    tests = root / "tests"
    tests.mkdir()
    mapping = {}
    for i in range(count):
        sid = f"scenario-{i:03d}"
        p = tests / f"test_row_{i:03d}.py"
        p.write_text(f"def test_row_{i:03d}():\n    assert True\n", encoding="utf-8")
        mapping[sid] = f"tests/{p.name}::test_row_{i:03d}"
    return mapping


def _model_answer(sid, mapping, status="SUPPORTED") -> ScenarioCoverageVerifyModelAnswer:
    row = ScenarioCoverageRowReview(
        scenario_id=sid,
        test_node_id=mapping[sid],
        assessment=status,
        evidence=f"checked test source for {sid}",
        when_proofs=[1] if status == "SUPPORTED" else [],
        outcome_proofs=[2] if status == "SUPPORTED" else [],
    )
    return ScenarioCoverageVerifyModelAnswer(
        verdict=(
            "SCENARIO_TEST_COVERAGE_PASS" if status == "SUPPORTED"
            else "SCENARIO_TEST_COVERAGE_FAIL" if status == "GAP"
            else "SCENARIO_TEST_COVERAGE_UNKNOWN"
        ),
        row_reviews=[row],
        gaps=([ScenarioCoverageGap(
            scenario_id=sid, gap_class="MISSING_TEST",
            evidence="no WHEN/THEN assertion",
        )] if status == "GAP" else []),
        block_reason="cannot inspect authoritative test" if status == "INSPECTION_BLOCKED" else "",
        summary="one independent row",
        local_hop_copy_block="one row only",
    )


def _answer(sid, mapping, status="SUPPORTED", digest=_SOURCE) -> ScenarioCoverageVerifyAnswer:
    return bind_scenario_coverage_authority(
        _model_answer(sid, mapping, status),
        plan_evidence_id=_PLAN,
        source_digest=digest,
    )


def _patch_proofs(monkeypatch, digest=_SOURCE):
    monkeypatch.setattr(row_single, "review_proof_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(row_receipts, "review_proof_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(row_single, "input_snapshot", lambda *args, **kwargs: {"source_digest": digest})
    monkeypatch.setattr(row_single, "get_delta_prompt", lambda **kwargs: (
        kwargs["evidence_ledger"][0]["scenario_id"]
    ))


class Adapter:
    def __init__(self, mapping, *, gap_ids=(), blocked_ids=(), crash_at=None):
        self.mapping = mapping
        self.gap_ids = set(gap_ids)
        self.blocked_ids = set(blocked_ids)
        self.crash_at = crash_at
        self.calls = []

    async def run(self, state, *, prompt, **kwargs):
        self.calls.append((prompt, kwargs, state))
        if len(self.calls) == self.crash_at:
            raise RuntimeError("SDK disconnected")
        sid = prompt
        assessment = (
            "GAP" if sid in self.gap_ids
            else "INSPECTION_BLOCKED" if sid in self.blocked_ids
            else "SUPPORTED"
        )
        answer = _model_answer(sid, self.mapping, assessment)
        n = len(self.calls)
        return AgentResult(
            text=answer.model_dump_json(), parsed=answer, status="finished",
            run_id=f"run-{n}", agent_id=f"agent-{n}",
        )


async def _run(root, adapter, mapping, *, pending=None, reused=None, execution="exec-1"):
    ids = list(mapping)
    pending = pending if pending is not None else ids
    reused = reused or {}
    return await row_single.audit_pending_independently(
        adapter=adapter, state={"repository_path": str(root), "run_uuid": execution,
                                "model": {"id": "composer-2.5", "options": []}},
        repo_root=root, workspace=str(root), change_id="demo",
        plan_ids=ids, mapping=mapping, plan_id=_PLAN,
        snapshot={"source_digest": _SOURCE},
        evidence_ledger=[{"scenario_id": sid} for sid in ids],
        pending=pending, reused=reused,
        row_digests={sid: sid.encode("utf-8").hex().ljust(64, "0")[:64] for sid in ids},
        execution_id=execution, validation_attempts=1,
    )


def _dig(sid):
    return sid.encode("utf-8").hex().ljust(64, "0")[:64]


@pytest.mark.asyncio
async def test_56_rows_56_independent_sdk_sessions_and_56_atomic_receipts(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 56)
    _patch_proofs(monkeypatch)
    adapter = Adapter(mapping)
    result, last, origin, counts = await _run(tmp_path, adapter, mapping)
    assert result.verdict == "SCENARIO_TEST_COVERAGE_PASS"
    assert len(result.row_reviews) == 56
    assert counts == {"sdk_calls": 56, "observations_saved": 56, "supported_receipts_saved": 56}
    assert len(set(x[0] for x in adapter.calls)) == 56
    assert all(call[1]["resume_agent_id"] is None for call in adapter.calls)
    assert all(call[1]["tools"] == ("read", "grep", "glob", "ls") for call in adapter.calls)
    # Individual SDK agents must not share run_dialogs canonical or inbox paths.
    from run_dialogs import infer_agent_folder_name
    agent_folders = {
        infer_agent_folder_name(
            state=call[2],
            stream_label=call[2]["stream_label"],
            stream_log=call[2].get("stream_log"),
        )
        for call in adapter.calls
    }
    assert len(agent_folders) == 56
    assert all(
        call[2]["run_dialog_agent"] == (
            "scenario_coverage_verify-" + row_single.row_key(call[0])
        )
        for call in adapter.calls
    )
    assert len({x["evidence_id"] for x in origin.values()}) == 56
    for sid in mapping:
        assert read_observation(tmp_path, change_id="demo", scenario_id=sid) is not None
        assert read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid,
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
        ) is not None


@pytest.mark.asyncio
async def test_one_failed_agent_does_not_erase_other_serial_receipts(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 4)
    _patch_proofs(monkeypatch)
    ids = list(mapping)
    with pytest.raises(ValueError, match="SCENARIO_ROW_QUEUE_INCOMPLETE"):
        await _run(tmp_path, Adapter(mapping, crash_at=3), mapping)
    for sid in (ids[0], ids[1], ids[3]):
        assert read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid,
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
        ) is not None
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[2],
        mapping=mapping, row_digest=_dig(ids[2]), plan_evidence_id=_PLAN,
    ) is None

    reused = {
        sid: read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid,
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
        )[0] for sid in (ids[0], ids[1], ids[3])
    }
    adapter = Adapter(mapping)
    answer, _, _, stats = await _run(
        tmp_path, adapter, mapping, pending=[ids[2]], reused=reused,
        execution="exec-2",
    )
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_PASS"
    assert len(adapter.calls) == 1 and stats["sdk_calls"] == 1


@pytest.mark.asyncio
async def test_default_pool_runs_one_agent_at_a_time_and_drains_queue(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 23)
    _patch_proofs(monkeypatch)
    class ConcurrentAdapter(Adapter):
        def __init__(self, mapping):
            super().__init__(mapping)
            self.active = 0
            self.peak = 0
            self.first_started = asyncio.Event()
            self.release = asyncio.Event()
        async def run(self, state, *, prompt, **kwargs):
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 1:
                self.first_started.set()
            try:
                await self.release.wait()
                return await super().run(state, prompt=prompt, **kwargs)
            finally:
                self.active -= 1
    adapter = ConcurrentAdapter(mapping)
    task = asyncio.create_task(_run(tmp_path, adapter, mapping))
    await asyncio.wait_for(adapter.first_started.wait(), timeout=4)
    assert adapter.active == 1
    assert not task.done()
    adapter.release.set()
    answer, _, _, stats = await asyncio.wait_for(task, timeout=10)
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_PASS"
    assert adapter.peak == 1
    assert stats["sdk_calls"] == 23
    assert len(adapter.calls) == 23


@pytest.mark.asyncio
async def test_pool_limit_parameter_rejects_parallel_override(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 1)
    _patch_proofs(monkeypatch)
    with pytest.raises(ValueError, match="SCENARIO_ROW_RUN_UNBOUND"):
        await row_single.audit_pending_independently(
            adapter=Adapter(mapping),
            state={"repository_path": str(tmp_path), "run_uuid": "exec-1"},
            repo_root=tmp_path, workspace=str(tmp_path), change_id="demo",
            plan_ids=list(mapping), mapping=mapping, plan_id=_PLAN,
            snapshot={"source_digest": _SOURCE},
            evidence_ledger=[{"scenario_id": sid} for sid in mapping],
            pending=list(mapping), reused={},
            row_digests={sid: _dig(sid) for sid in mapping},
            execution_id="exec-1", validation_attempts=1,
            max_concurrency=2,
        )


@pytest.mark.asyncio
async def test_row_logs_unique_and_not_shared_during_serial_audits(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 3)
    _patch_proofs(monkeypatch)
    class LogAdapter(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            assert state["stream_log"] is not None
            assert "scenario-row-streams" in state["stream_log"]
            return await super().run(state, prompt=prompt, **kwargs)
    adapter = LogAdapter(mapping)
    ids = list(mapping)
    await row_single.audit_pending_independently(
        adapter=adapter,
        state={
            "repository_path": str(tmp_path), "run_uuid": "exec-log",
            "model": {"id": "composer-2.5"},
            "stream_log": str(tmp_path / "scenario.full.live.ndjson"),
        },
        repo_root=tmp_path, workspace=str(tmp_path), change_id="demo",
        plan_ids=ids, mapping=mapping, plan_id=_PLAN,
        snapshot={"source_digest": _SOURCE},
        evidence_ledger=[{"scenario_id": sid} for sid in ids],
        pending=ids, reused={}, row_digests={sid: _dig(sid) for sid in ids},
        execution_id="exec-log", validation_attempts=1,
    )
    logs = [call[2]["stream_log"] for call in adapter.calls]
    assert len(logs) == len(set(logs)) == 3


@pytest.mark.asyncio
async def test_gap_and_unknown_recorded_but_not_reused_as_pass(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 3)
    _patch_proofs(monkeypatch)
    ids = list(mapping)
    adapter = Adapter(mapping, gap_ids=[ids[1]], blocked_ids=[ids[2]])
    answer, _, _, stats = await _run(tmp_path, adapter, mapping)
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert len(answer.gaps) == 1 and len(answer.row_reviews) == 3
    assert stats["supported_receipts_saved"] == 1
    for sid in ids:
        assert read_observation(tmp_path, change_id="demo", scenario_id=sid)
    for sid in ids[1:]:
        assert read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid,
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
        ) is None


@pytest.mark.asyncio
async def test_stale_source_between_sdk_and_write_blocks_checkpoint(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 1)
    _patch_proofs(monkeypatch, digest="0" * 64)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_HOP"):
        await _run(tmp_path, Adapter(mapping), mapping)
    sid = next(iter(mapping))
    assert read_observation(tmp_path, change_id="demo", scenario_id=sid) is None


@pytest.mark.asyncio
async def test_wrong_row_cannot_publish_receipt(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 1)
    _patch_proofs(monkeypatch)
    class MismatchAdapter:
        async def run(self, *_args, **_kwargs):
            sid = next(iter(mapping))
            answer = _model_answer(sid, mapping)
            answer.row_reviews[0].scenario_id = "other"
            return AgentResult(
                text=answer.model_dump_json(), parsed=answer, status="finished",
                agent_id="agent-x", run_id="run-x",
            )
    with pytest.raises(ValueError, match="ROW_ID_MISMATCH"):
        await _run(tmp_path, MismatchAdapter(), mapping)
    assert not list((tmp_path / "opsx/receipts/demo").rglob("*.json"))


def test_tampering_with_one_row_does_not_affect_other_receipts(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 2)
    _patch_proofs(monkeypatch)
    sid_a, sid_b = list(mapping)
    for sid in mapping:
        row = _answer(sid, mapping).row_reviews[0]
        assert save_supported_row(
            tmp_path, change_id="demo", row=row,
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
            origin_execution_id="exec-1", evidence_id=f"run:{sid}",
        )
    corrupt = tmp_path / "opsx/receipts/demo/check-receipts" / (row_check(sid_a) + ".json")
    receipt = json.loads(corrupt.read_text(encoding="utf-8"))
    receipt["result"]["row"]["evidence"] = "tampered"
    corrupt.write_text(json.dumps(receipt), encoding="utf-8")
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=sid_a, mapping=mapping,
        row_digest=_dig(sid_a), plan_evidence_id=_PLAN,
    ) is None
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=sid_b, mapping=mapping,
        row_digest=_dig(sid_b), plan_evidence_id=_PLAN,
    ) is not None


def test_changing_only_one_row_digest_invalidates_only_that_row(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 2)
    _patch_proofs(monkeypatch)
    ids = list(mapping)
    for sid in ids:
        assert save_supported_row(
            tmp_path, change_id="demo", row=_answer(sid, mapping).row_reviews[0],
            mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
            origin_execution_id="exec-1", evidence_id=f"run:{sid}",
        )
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[0], mapping=mapping,
        row_digest="9" * 64, plan_evidence_id=_PLAN,
    ) is None
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[1], mapping=mapping,
        row_digest=_dig(ids[1]), plan_evidence_id=_PLAN,
    ) is not None


@pytest.mark.parametrize("mutation", ["plan", "mapping"])
def test_row_plan_or_mapping_change_fails_closed(tmp_path, monkeypatch, mutation):
    mapping = _workspace(tmp_path, 1)
    _patch_proofs(monkeypatch)
    sid = next(iter(mapping))
    assert save_supported_row(
        tmp_path, change_id="demo", row=_answer(sid, mapping).row_reviews[0],
        mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
        origin_execution_id="exec-1", evidence_id="run:agent",
    )
    if mutation == "plan":
        plan_id = "new-plan"
    else:
        plan_id = _PLAN
        mapping[sid] = "tests/another_test.py::test"
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=sid,
        mapping=mapping, row_digest=_dig(sid), plan_evidence_id=plan_id,
    ) is None


def test_observation_checksum_detects_corruption(tmp_path):
    mapping = _workspace(tmp_path, 1)
    sid = next(iter(mapping))
    row = _answer(sid, mapping).row_reviews[0]
    write_observation(
        tmp_path, change_id="demo", row=row, row_digest=_dig(sid),
        source_digest=_SOURCE, plan_evidence_id=_PLAN,
        origin_execution_id="exec-1", evidence_id="run:agent",
    )
    good = read_observation(tmp_path, change_id="demo", scenario_id=sid)
    assert good["assessment"] == "SUPPORTED"
    file = next((tmp_path / "opsx/receipts/demo/scenario-rows").glob("*.json"))
    item = json.loads(file.read_text(encoding="utf-8"))
    item["assessment"] = "GAP"
    file.write_text(json.dumps(item), encoding="utf-8")
    assert read_observation(tmp_path, change_id="demo", scenario_id=sid) is None



@pytest.mark.asyncio
async def test_console_progress_shows_queue_start_done_and_final_row_counts(
    tmp_path, monkeypatch, capsys,
):
    mapping = _workspace(tmp_path, 12)
    _patch_proofs(monkeypatch)
    adapter = Adapter(mapping)
    result, _, _, counts = await _run(tmp_path, adapter, mapping)
    err = capsys.readouterr().err
    assert "[OPSX scenario-coverage] QUEUE" in err
    assert 'total=12' in err
    assert "concurrency=1" in err
    assert err.count("[OPSX scenario-coverage] START") == 12
    assert err.count("[OPSX scenario-coverage] DONE") == 12
    assert "assessment=\"SUPPORTED\"" in err
    assert "receipt=true" in err
    assert "finished=12" in err
    assert result.verdict == "SCENARIO_TEST_COVERAGE_PASS"
    assert counts["supported_receipts_saved"] == 12


@pytest.mark.asyncio
async def test_console_progress_marks_failed_row_and_paused_aggregate(
    tmp_path, monkeypatch, capsys,
):
    mapping = _workspace(tmp_path, 4)
    _patch_proofs(monkeypatch)
    with pytest.raises(ValueError, match="QUEUE_INCOMPLETE"):
        await _run(tmp_path, Adapter(mapping, crash_at=3), mapping)
    err = capsys.readouterr().err
    assert err.count("[OPSX scenario-coverage] START") == 4
    assert err.count("[OPSX scenario-coverage] DONE") == 3
    assert err.count("[OPSX scenario-coverage] ERROR") == 1
    assert "[OPSX scenario-coverage] PAUSED" in err
    assert "failed=1" in err



@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "RateLimitError: resource_exhausted: 30 requests per minute for get_models endpoint",
    "InternalServerError: 502 Bad Gateway",
    "NetworkError: Bridge request failed: RemoteProtocolError incomplete chunked read",
    "InternalServerError: Agent x already has active run",
    "InternalServerError: Cannot wait on a detached running run",
    "RuntimeError: CURSOR_PROVIDER_OVERLOAD_PAUSE",
])
async def test_systemic_overload_opens_circuit_and_leaves_queued_rows_untouched(
    tmp_path, monkeypatch, capsys, message,
):
    mapping = _workspace(tmp_path, 56)
    _patch_proofs(monkeypatch)
    class OverloadedAdapter(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            self.calls.append((prompt, kwargs, state))
            raise RuntimeError(message)
    adapter = OverloadedAdapter(mapping)
    with pytest.raises(ValueError, match="SCENARIO_ROW_QUEUE_INCOMPLETE") as exc:
        await _run(tmp_path, adapter, mapping)
    assert "PROVIDER_OVERLOAD_PAUSE" in str(exc.value)
    assert "RECOVERY:" in str(exc.value)
    # The first provider failure prevents further SDK jobs from being pulled.
    assert len(adapter.calls) == 1
    output = capsys.readouterr().err
    assert "[OPSX scenario-coverage] CIRCUIT_OPEN" in output
    assert "[OPSX scenario-coverage] PAUSED" in output
    assert "queued=55" in output
    assert "verified_receipts=0" in output
    assert not list(tmp_path.rglob("scenario_supported_*.json"))


@pytest.mark.asyncio
async def test_overload_leaves_completed_row_receipts_usable_after_restart(
    tmp_path, monkeypatch,
):
    mapping = _workspace(tmp_path, 4)
    _patch_proofs(monkeypatch)
    ids = list(mapping)
    class DelayedOverload(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            if prompt == ids[2]:
                self.calls.append((prompt, kwargs, state))
                raise RuntimeError("RateLimitError: resource_exhausted: get_models endpoint")
            return await super().run(state, prompt=prompt, **kwargs)
    with pytest.raises(ValueError, match="PROVIDER_OVERLOAD_PAUSE"):
        await _run(tmp_path, DelayedOverload(mapping), mapping, 
                   # Force first two receipts to checkpoint before 429.
                   pending=ids, reused={})
    for sid in ids[:2]:
        assert read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid, mapping=mapping,
            row_digest=_dig(sid), plan_evidence_id=_PLAN,
        ) is not None
    # Scenario 3 is retryable, scenario 4 remains queued; neither is a PASS.
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=ids[2], mapping=mapping,
        row_digest=_dig(ids[2]), plan_evidence_id=_PLAN,
    ) is None
    reused = {
        sid: read_supported_row(
            tmp_path, change_id="demo", scenario_id=sid, mapping=mapping,
            row_digest=_dig(sid), plan_evidence_id=_PLAN,
        )[0] for sid in ids[:2]
    }
    next_adapter = Adapter(mapping)
    answer, _, _, stats = await _run(
        tmp_path, next_adapter, mapping, pending=ids[2:], reused=reused,
        execution="exec-restored",
    )
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_PASS"
    assert stats["sdk_calls"] == 2
    assert len(next_adapter.calls) == 2


@pytest.mark.asyncio
async def test_individual_scenario_proof_failure_does_not_open_provider_circuit(
    tmp_path, monkeypatch, capsys,
):
    mapping = _workspace(tmp_path, 4)
    _patch_proofs(monkeypatch)
    ids = list(mapping)
    class InvalidProof(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            if prompt == ids[1]:
                self.calls.append((prompt, kwargs, state))
                raise ValueError("SCENARIO_ROW_PROOF_INVALID")
            return await super().run(state, prompt=prompt, **kwargs)
    adapter = InvalidProof(mapping)
    with pytest.raises(ValueError, match="QUEUE_INCOMPLETE"):
        await _run(tmp_path, adapter, mapping)
    assert len(adapter.calls) == 4
    output = capsys.readouterr().err
    assert "CIRCUIT_OPEN" not in output

@pytest.mark.parametrize("count", [1, 2, 3, 8])
def test_verify_single_accepts_multiple_distinct_gaps_for_one_scenario(tmp_path, count):
    mapping = _workspace(tmp_path, 1)
    sid = next(iter(mapping))
    answer = _answer(sid, mapping, "GAP")
    answer.gaps = [
        ScenarioCoverageGap(
            scenario_id=sid, gap_class="MISSING_ASSERTION", evidence=f"gap-{i}",
        ) for i in range(count)
    ]
    row = row_single.verify_single_answer(
        answer=answer, scenario_id=sid, mapped_node=mapping[sid],
        plan_id=_PLAN, source_digest=_SOURCE,
    )
    assert row.assessment == "GAP"
    assert len(answer.gaps) == count


@pytest.mark.parametrize("defect", ["missing_gap", "foreign_gap"])
def test_verify_single_fails_closed_on_gap_identity_mismatch(tmp_path, defect):
    mapping = _workspace(tmp_path, 1)
    sid = next(iter(mapping))
    answer = _answer(sid, mapping, "GAP")
    if defect == "missing_gap":
        answer.gaps = []
    elif defect == "foreign_gap":
        answer.gaps += [ScenarioCoverageGap(
            scenario_id="other", gap_class="NO_ASSERT", evidence="wrong scenario",
        )]
    with pytest.raises(ValueError, match="SCENARIO_ROW_"):
        row_single.verify_single_answer(
            answer=answer, scenario_id=sid, mapped_node=mapping[sid],
            plan_id=_PLAN, source_digest=_SOURCE,
        )


@pytest.mark.asyncio
async def test_serial_sdk_multi_gap_retains_all_findings_without_pass_receipt(tmp_path, monkeypatch):
    mapping = _workspace(tmp_path, 1)
    _patch_proofs(monkeypatch)
    sid = next(iter(mapping))

    class MultiGapAdapter(Adapter):
        async def run(self, state, *, prompt, **kwargs):
            self.calls.append((prompt, kwargs, state))
            answer = _model_answer(prompt, self.mapping, "GAP")
            answer.gaps = [
                ScenarioCoverageGap(scenario_id=prompt, gap_class="MISSING_WHEN",
                                    evidence="no prerequisite"),
                ScenarioCoverageGap(scenario_id=prompt, gap_class="MISSING_THEN",
                                    evidence="no assertion"),
            ]
            return AgentResult(
                text=answer.model_dump_json(), parsed=answer, status="finished",
                run_id="sdk-multi", agent_id="agent-multi",
            )

    adapter = MultiGapAdapter(mapping)
    answer, _, _, stats = await _run(tmp_path, adapter, mapping)
    assert len(adapter.calls) == 1
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_FAIL"
    assert len(answer.gaps) == 2
    assert stats == {"sdk_calls": 1, "observations_saved": 1, "supported_receipts_saved": 0}
    obs = read_observation(tmp_path, change_id="demo", scenario_id=sid)
    assert [g["gap_class"] for g in obs["gaps"]] == ["MISSING_WHEN", "MISSING_THEN"]
    assert read_supported_row(
        tmp_path, change_id="demo", scenario_id=sid,
        mapping=mapping, row_digest=_dig(sid), plan_evidence_id=_PLAN,
    ) is None


def test_gap_observation_rejects_foreign_finding_without_creating_file(tmp_path):
    mapping = _workspace(tmp_path, 1)
    sid = next(iter(mapping))
    row = _answer(sid, mapping, "GAP").row_reviews[0]
    with pytest.raises(ValueError, match="ROW_OBSERVATION_GAPS_IDENTITY_MISMATCH"):
        write_observation(
            tmp_path, change_id="demo", row=row, row_digest=_dig(sid),
            source_digest=_SOURCE, plan_evidence_id=_PLAN,
            origin_execution_id="exec-1", evidence_id="sdk:agent",
            gaps=[ScenarioCoverageGap(
                scenario_id="other", gap_class="BAD", evidence="foreign evidence",
            )],
        )
    assert not (tmp_path / "opsx/receipts/demo").exists()
