"""Handoff при BLOCKED test_repair: outside_restrict_allow → COMPLETE + deny path."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.adapters.abc_adapter import AgentRunContractError
from agents.abc_agent import ABCAgent
from agents.config import ReadScopeViolation, resolve_read_scope
from agents.verify.group_error.schemas import GroupErrorResult
from agents.scope_tool_payload import (
    read_scope_violation_from_stream_record,
    read_scope_violation_from_tool_payload,
)
from agents.verify.test_repair.scope_denial_handoff import (
    build_scope_denial_handoff_patch,
    maybe_scope_denial_handoff_from_repair,
    parse_scope_denial_from_repair_text,
    scope_denial_summary_from_violation,
    synthetic_blocked_test_repair_agent_result,
    is_test_repair_outside_allow_handoff,
)
from agents.verify.test_repair.schemas import TestRepairResult


def test_parse_scope_denial_from_summary() -> None:
    text = (
        "READ_SCOPE_VIOLATION tool=edit path='tools/openspec/consultation_baseline/"
        "pinned/manifest.json' rule='outside_restrict_allow'"
    )
    parsed = parse_scope_denial_from_repair_text(text)
    assert parsed is not None
    path, rule, tool = parsed
    assert rule == "outside_restrict_allow"
    assert tool == "edit"
    assert "pinned/manifest.json" in path.replace("\\", "/")


def test_scope_denial_handoff_records_complete_diagnostics() -> None:
    state = {
        "change_id": "visual",
        "repository_path": ".",
        "quality_gate_diagnostics": {
            "pytest": [
                "pytest: tests/scenarios/consultation-funnel-baseline/"
                "test_x.py::test_export_and_pass - PinnedSourceLoadError",
            ],
            "pyright": [],
            "mypy": [],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": ["legacy partial"],
        },
    }
    patch = build_scope_denial_handoff_patch(
        state,
        path="tools/openspec/consultation_baseline/pinned/manifest.json",
        rule="outside_restrict_allow",
        tool="edit",
    )
    diag = patch["quality_gate_diagnostics"]
    assert diag["input_integrity_status"] == "COMPLETE"
    denials = diag.get("repair_scope_denials") or []
    assert denials and denials[0]["rule"] == "outside_restrict_allow"
    analysis = GroupErrorResult.model_validate(patch["analysis_result"])
    assert analysis.input_integrity.status == "COMPLETE"
    assert patch["group_error_execution_status"] == "deterministic_gate_only"
    assert "REPAIR_SCOPE_DENIED" in str(patch.get("owner_attention") or "")


def test_maybe_handoff_ignores_fixed_status() -> None:
    assert (
        maybe_scope_denial_handoff_from_repair(
            {},
            status="FIXED",
            summary="outside_restrict_allow on edit",
            notes=None,
        )
        == {}
    )


def test_soft_mcp_read_manifest_payload_parses_violation() -> None:
    """Форма live-log: READ manifest → success + JSON READ_SCOPE_VIOLATION в text."""
    payload = {
        "status": "success",
        "value": {
            "content": [
                {
                    "text": {
                        "text": (
                            '{"ok": false, "code": "READ_SCOPE_VIOLATION", '
                            '"rule": "outside_restrict_allow", '
                            '"path": "tools/openspec/consultation_baseline/'
                            'pinned/manifest.json"}'
                        ),
                    },
                },
            ],
        },
    }
    violation = read_scope_violation_from_tool_payload(tool="read", payload=payload)
    assert violation is not None
    assert violation.rule == "outside_restrict_allow"
    assert violation.path.endswith("pinned/manifest.json")


def test_maybe_handoff_blocked_with_soft_tool_payload_recorded() -> None:
    """Voluntary BLOCKED после soft read deny: prose без path=', violation из hop."""
    payload = {
        "status": "success",
        "value": {
            "content": [
                {
                    "text": {
                        "text": (
                            '{"ok": false, "code": "READ_SCOPE_VIOLATION", '
                            '"rule": "outside_restrict_allow", '
                            '"path": "tools/openspec/consultation_baseline/'
                            'pinned/manifest.json"}'
                        ),
                    },
                },
            ],
        },
    }
    recorded = read_scope_violation_from_tool_payload(tool="read", payload=payload)
    assert recorded is not None
    summary = (
        "Когорта: PinnedSourceLoadError; git-pin sync needs tools/openspec pinned bundle "
        "outside hop ALLOW; gate excerpt cites READ_SCOPE_VIOLATION on manifest.json."
    )
    assert parse_scope_denial_from_repair_text(summary) is None
    patch = maybe_scope_denial_handoff_from_repair(
        {
            "change_id": "visual",
            "quality_gate_diagnostics": {
                "pytest": ["pytest: tests/scenarios/x.py::test_export_and_pass - fail"],
            },
        },
        status="BLOCKED",
        summary=summary,
        notes=None,
        recorded_violation=recorded,
    )
    assert "REPAIR_SCOPE_DENIED" in str(patch.get("owner_attention") or "")
    denials = (patch.get("quality_gate_diagnostics") or {}).get("repair_scope_denials") or []
    assert denials[0]["rule"] == "outside_restrict_allow"
    assert "pinned/manifest.json" in denials[0]["path"]


def test_stream_record_tool_call_completed_extracts_soft_violation() -> None:
    record = {
        "kind": "tool_call",
        "name": "read",
        "status": "completed",
        "result": {
            "status": "success",
            "value": {
                "content": [
                    {
                        "text": {
                            "text": (
                                '{"code": "READ_SCOPE_VIOLATION", '
                                '"rule": "outside_restrict_allow", '
                                '"path": "tools/x/pinned/manifest.json"}'
                            ),
                        },
                    },
                ],
            },
        },
    }
    violation = read_scope_violation_from_stream_record(record)
    assert violation is not None
    assert violation.tool == "read"


def test_maybe_handoff_blocked_with_recorded_violation_without_parseable_prose() -> None:
    """Voluntary BLOCKED после одной коррекции: prose без path='…', violation из hop."""
    repo = Path("C:/Users/vkita/my_code/opsx-implement")
    exc = ReadScopeViolation(
        tool="edit",
        path=str(
            repo / "tools/openspec/consultation_baseline/pinned/manifest.json",
        ),
        rule="outside_restrict_allow",
    )
    prose = (
        "Cohort reproduces SourceLoadError; repair needs refresh pinned/manifest.json "
        "under tools/ which is outside this hop-scope ALLOW; prior edit was rejected "
        "outside_restrict_allow."
    )
    assert parse_scope_denial_from_repair_text(prose) is None
    patch = maybe_scope_denial_handoff_from_repair(
        {
            "change_id": "visual",
            "quality_gate_diagnostics": {
                "pytest": ["pytest: tests/scenarios/x.py::test_export_and_pass - fail"],
            },
        },
        status="BLOCKED",
        summary=prose,
        notes=None,
        recorded_violation=exc,
        repo_root=repo,
    )
    assert "REPAIR_SCOPE_DENIED" in str(patch.get("owner_attention") or "")
    denials = (patch.get("quality_gate_diagnostics") or {}).get("repair_scope_denials") or []
    assert denials[0]["rule"] == "outside_restrict_allow"
    assert "pinned/manifest.json" in denials[0]["path"]


def test_maybe_handoff_blocked_without_violation_stays_empty() -> None:
    patch = maybe_scope_denial_handoff_from_repair(
        {
            "change_id": "visual",
            "quality_gate_diagnostics": {"pytest": ["pytest: x - fail"]},
        },
        status="BLOCKED",
        summary="Cannot fix without owner decision on architecture.",
        notes=None,
        recorded_violation=None,
    )
    assert patch == {}


def test_scope_denial_summary_normalizes_absolute_path() -> None:
    repo = Path("C:/Users/vkita/my_code/opsx-implement")
    exc = ReadScopeViolation(
        tool="edit",
        path=str(
            repo / "tools/openspec/consultation_baseline/pinned/manifest.json",
        ),
        rule="outside_restrict_allow",
    )
    summary = scope_denial_summary_from_violation(exc, repo_root=repo)
    assert "tools/openspec/consultation_baseline/pinned/manifest.json" in summary
    assert parse_scope_denial_from_repair_text(summary) is not None


def test_synthetic_blocked_result_parses_as_test_repair_blocked() -> None:
    repo = Path("C:/Users/vkita/my_code/opsx-implement")
    exc = ReadScopeViolation(
        tool="edit",
        path=str(
            repo / "tools/openspec/consultation_baseline/pinned/manifest.json",
        ),
        rule="outside_restrict_allow",
        agent_id="agent-tr",
        run_id="run-tr",
    )
    agent_result = synthetic_blocked_test_repair_agent_result(exc, repo_root=repo)
    parsed = TestRepairResult.model_validate_json(agent_result.text)
    assert parsed.status == "BLOCKED"
    patch = maybe_scope_denial_handoff_from_repair(
        {
            "change_id": "visual",
            "quality_gate_diagnostics": {"pytest": ["pytest: x - fail"]},
        },
        status=parsed.status,
        summary=parsed.summary,
        notes=parsed.notes,
    )
    assert "REPAIR_SCOPE_DENIED" in str(patch.get("owner_attention") or "")
    denials = (patch.get("quality_gate_diagnostics") or {}).get("repair_scope_denials") or []
    assert denials[0]["hop"] == "test_repair"
    assert denials[0]["rule"] == "outside_restrict_allow"


def test_is_test_repair_outside_allow_handoff_predicate() -> None:
    exc = ReadScopeViolation(tool="edit", path="x", rule="outside_restrict_allow")
    assert is_test_repair_outside_allow_handoff("test_repair", exc)
    assert not is_test_repair_outside_allow_handoff("apply_red", exc)
    assert not is_test_repair_outside_allow_handoff(
        "test_repair",
        ReadScopeViolation(tool="read", path=".", rule="repo_root_scan"),
    )


class _AlwaysOutsideAllowAdapter:
    async def _run_turn(self, state, **kwargs):  # type: ignore[no-untyped-def]
        _ = state, kwargs
        raise ReadScopeViolation(
            tool="edit",
            path="C:/Users/vkita/my_code/opsx-implement/tools/openspec/consultation_baseline/pinned/manifest.json",
            rule="outside_restrict_allow",
            agent_id="agent-tr-2",
            run_id="run-tr-2",
        )


@pytest.mark.asyncio
async def test_test_repair_second_outside_allow_returns_blocked_not_contract_error() -> None:
    from agents.adapters.abc_adapter import ABCAdapter

    class _Adapter(_AlwaysOutsideAllowAdapter, ABCAdapter):
        def __init__(self) -> None:
            self.calls = 0

        async def _run_turn(self, state, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return await super()._run_turn(state, **kwargs)

    adapter = _Adapter()
    scope = resolve_read_scope("test_repair")
    state = {
        "repository_path": "C:/Users/vkita/my_code/opsx-implement",
        "change_id": "visual",
        "model": {"id": "x"},
    }
    result = await ABCAgent.submit_turn(
        adapter,
        state,
        prompt="repair test",
        tools=("read", "edit"),
        read_scope=scope,
        resume_agent_id=None,
        agent_key="test_repair",
    )
    assert adapter.calls == 2
    parsed = TestRepairResult.model_validate_json(result.text)
    assert parsed.status == "BLOCKED"
    assert "manifest.json" in parsed.summary


@pytest.mark.asyncio
async def test_test_repair_repo_root_scan_still_contract_error() -> None:
    from agents.adapters.abc_adapter import ABCAdapter

    class _Adapter(ABCAdapter):
        async def _run_turn(self, state, **kwargs):  # type: ignore[no-untyped-def]
            _ = state, kwargs
            raise ReadScopeViolation(
                tool="read",
                path="C:/Users/vkita/my_code/opsx-implement",
                rule="repo_root_scan",
                agent_id="agent-tr-3",
                run_id="run-tr-3",
            )

    with pytest.raises(AgentRunContractError) as err:
        await ABCAgent.submit_turn(
            _Adapter(),
            {
                "repository_path": "C:/Users/vkita/my_code/opsx-implement",
                "model": {"id": "x"},
            },
            prompt="x",
            tools=("read", "edit"),
            read_scope=resolve_read_scope("test_repair"),
            resume_agent_id=None,
            agent_key="test_repair",
        )
    assert err.value.code == "TOOL_SCOPE_VIOLATION"
    assert err.value.attempt == 1
