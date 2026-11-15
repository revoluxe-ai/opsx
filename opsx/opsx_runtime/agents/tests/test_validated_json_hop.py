"""Unit tests for JSON validation retry in ``ABCAdapter.run``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
from pydantic import BaseModel, ConfigDict

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import ABCAdapter, AgentResult, AgentRunContractError
from agents.budget.guard import BudgetExceeded
from agents.response_schema import (
    SCHEMA_SECTION_MARKER,
    final_json_output_contract,
    response_schema_section,
)


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True


class _FlakyAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.agent_id = "agent-flaky-1"

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, tools, prompt, payload_model, read_scope
        self.calls += 1
        if self.calls < 3:
            return AgentResult(
                text='{"severity": "bad"}',
                status="finished",
                agent_id=self.agent_id,
                run_id=f"run-{self.calls}",
            )
        return AgentResult(
            text='{"ok": true}',
            status="finished",
            agent_id=self.agent_id,
            run_id="run-ok",
        )


class _BudgetThenOkAdapter(ABCAdapter):
    """Первый turn — intra-run BudgetExceeded; второй — валидный JSON."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, tools, payload_model, read_scope, resume_agent_id
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls == 1:
            raise BudgetExceeded("max tool invocations per hop exceeded")
        return AgentResult(
            text='{"ok": true}',
            status="finished",
            agent_id="agent-budget-retry",
            run_id="run-ok",
        )


@pytest.mark.asyncio
async def test_execute_validated_retries_on_resource_limit_with_continuation(
    tmp_path: Path,
) -> None:
    adapter = _BudgetThenOkAdapter()
    state = {
        "output_dir": str(tmp_path),
        "mission_id": "budget-retry-mission",
        "repository_path": str(tmp_path),
    }
    result = await ABCAgent.execute_validated(
        adapter,
        state,
        prompt="Fix the failing test.",
        tools=(),
        payload_model=_Answer,
        max_validation_attempts=4,
    )
    assert adapter.calls == 2
    assert result.parsed is not None
    assert result.parsed.ok is True
    assert "HOP CONTINUATION" in adapter.prompts[1]
    assert "RESOURCE_LIMIT" in adapter.prompts[1]
    assert "Do not restart broad codebase exploration" in adapter.prompts[1]


@pytest.mark.asyncio
async def test_adapter_run_stops_repeated_schema_failure_before_third_sdk_call():
    adapter = _FlakyAdapter()
    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"workspace": "/tmp", "repository_path": "/tmp", "model": {"id": "x"}},
            prompt="initial",
            tools=(),
            payload_model=_Answer,
            format_instructions='{"ok": true}',
            max_validation_attempts=4,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert error.value.attempt == 2
    assert error.value.agent_id == adapter.agent_id
    assert adapter.calls == 2  # Third turn would have been a false convergence loop



class _PromptCaptureAdapter(ABCAdapter):
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []
        self.resume_ids: list[str | None] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = read_scope
        self.prompts.append(prompt)
        self.resume_ids.append(resume_agent_id)
        return AgentResult(
            text=self.responses[len(self.prompts) - 1],
            status="finished",
            agent_id="agent-unchanged",
            run_id=f"run-{len(self.prompts)}",
        )


@pytest.mark.asyncio
async def test_shared_json_tail_follows_large_role_context_without_duplicate_schema() -> None:
    adapter = _PromptCaptureAdapter(['{"ok": true}'])
    # impl_verify historically embeds the schema BEFORE the large artifact dump.
    prompt = (
        "Ты — PHASE_IMPL, проверяй код.\n"
        + response_schema_section(_Answer)
        + "\n\nINPUT CONTEXT — planning artifacts:\n"
        + ("large-context-" * 10_000)
    )
    result = await adapter.run(
        {"repository_path": "/r"}, prompt=prompt, tools=(),
        payload_model=_Answer, max_validation_attempts=1,
    )
    sent = adapter.prompts[0]
    assert sent.count(SCHEMA_SECTION_MARKER) == 1
    assert sent.startswith("Ты — PHASE_IMPL, проверяй код.\n\n")
    assert "INPUT CONTEXT — planning artifacts:\n" in sent
    assert "_Answer" not in sent
    assert sent.endswith(final_json_output_contract())
    assert sent.rfind("INPUT CONTEXT") < sent.rfind(SCHEMA_SECTION_MARKER)
    assert sent.rfind(SCHEMA_SECTION_MARKER) < sent.rfind(final_json_output_contract())
    assert result.parsed is not None and result.parsed.ok is True


@pytest.mark.asyncio
async def test_schema_is_added_once_when_role_prompt_does_not_have_one() -> None:
    adapter = _PromptCaptureAdapter(['{"ok": true}'])
    await adapter.run(
        {"repository_path": "/r"},
        prompt="You are an independent reviewer.",
        tools=(), payload_model=_Answer,
    )
    sent = adapter.prompts[0]
    assert sent.count(SCHEMA_SECTION_MARKER) == 1
    assert sent.endswith(final_json_output_contract())


@pytest.mark.asyncio
async def test_strict_json_contract_is_injected_when_retry_disabled() -> None:
    adapter = _PromptCaptureAdapter(['{"ok": true}'])
    await adapter.run(
        {"repository_path": "/r"}, prompt="Short task",
        tools=(), payload_model=_Answer, validation_retry=False,
    )
    assert adapter.prompts == [
        "Short task\n\n"
        + response_schema_section(_Answer)
        + "\n\n"
        + final_json_output_contract()
    ]


@pytest.mark.asyncio
async def test_json_correction_resume_gets_same_tail_and_no_new_agent() -> None:
    adapter = _PromptCaptureAdapter(['{"ok":', '{"ok": true}'])
    outcome = await adapter.run(
        {"repository_path": "/r"}, prompt="Audit existing behavior",
        tools=(), payload_model=_Answer, max_validation_attempts=2,
    )
    assert outcome.validation_attempts == 2
    assert adapter.resume_ids == [None, "agent-unchanged"]
    assert len(adapter.prompts) == 2
    for sent in adapter.prompts:
        assert sent.endswith(final_json_output_contract())
    assert "не открывай файлы заново" in adapter.prompts[1]
    assert "## SCHEMA RETRY" in adapter.prompts[1]
    assert adapter.prompts[1].count(SCHEMA_SECTION_MARKER) == 2


@pytest.mark.asyncio
async def test_non_schema_turn_keeps_its_prompt_unchanged() -> None:
    adapter = _PromptCaptureAdapter(["free-form reply"])
    result = await adapter.run(
        {"repository_path": "/r"}, prompt="Free-form plain text",
        tools=(), payload_model=None,
    )
    assert adapter.prompts == ["Free-form plain text"]
    assert result.text == "free-form reply"


class _ProofAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when_proofs: list[int]


@pytest.mark.asyncio
async def test_common_agent_layer_repairs_semantic_proof_with_same_session() -> None:
    adapter = _PromptCaptureAdapter([
        '{"when_proofs":[25,26,27]}',
        '{"when_proofs":[25]}',
    ])

    def validate_proof(parsed: BaseModel) -> None:
        if len(parsed.when_proofs) != 1:
            raise ValueError("PROOF_CLAUSES_MISMATCH: WHEN expected=1, got=3")

    result = await adapter.run(
        {"repository_path": "/r"}, prompt="Audit one scenario", tools=(),
        payload_model=_ProofAnswer, post_parse_validate=validate_proof,
        max_validation_attempts=3,
    )

    assert result.parsed is not None
    assert result.parsed.when_proofs == [25]
    assert result.validation_attempts == 2
    assert adapter.resume_ids == [None, "agent-unchanged"]
    assert "PROOF_CLAUSES_MISMATCH" in adapter.prompts[1]
    assert "не открывай файлы заново" in adapter.prompts[1]
    assert "## SCHEMA RETRY" in adapter.prompts[1]


@pytest.mark.asyncio
async def test_common_agent_semantic_retry_stops_identical_finding_without_false_pass() -> None:
    adapter = _PromptCaptureAdapter([
        '{"when_proofs":[25,26,27]}',
        '{"when_proofs":[25,26]}',
    ])

    def validate_proof(parsed: BaseModel) -> None:
        if len(parsed.when_proofs) != 1:
            raise ValueError("PROOF_CLAUSES_MISMATCH: WHEN expected=1")

    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit one scenario", tools=(),
            payload_model=_ProofAnswer, post_parse_validate=validate_proof,
            max_validation_attempts=2,
        )

    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert error.value.agent_id == "agent-unchanged"
    assert error.value.attempt == 2
    assert adapter.resume_ids == [None, "agent-unchanged"]
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
async def test_actual_scenario_proof_validator_corrects_bad_cardinality(
    tmp_path: Path,
) -> None:
    from agents.verify.scenario_coverage_verify.evidence_contract import review_proof_error
    from agents.verify.scenario_coverage_verify.schemas import (
        ScenarioCoverageRowReview,
        ScenarioCoverageVerifyModelAnswer,
    )

    spec = tmp_path / "openspec/changes/demo/specs/channel/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### Requirement: shared channel\n"
        "#### Scenario: first entry\n"
        "- **WHEN** a MAX user enters the funnel\n"
        "- **THEN** the MAX state is created\n"
        "- **AND** Telegram state remains isolated\n",
        encoding="utf-8",
    )
    test_file = tmp_path / "tests/test_channel.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_route():\n"
        "    channel = 'max'\n"
        "    assert channel == 'max'\n"
        "    assert channel != 'telegram'\n",
        encoding="utf-8",
    )
    scenario_id = "openspec/changes/demo/specs/channel/spec.md::first entry"
    test_node = "tests/test_channel.py::test_route"

    def answer(when: list[int]) -> str:
        return json.dumps({
            "verdict": "SCENARIO_TEST_COVERAGE_PASS",
            "summary": "Checked mapped test",
            "local_hop_copy_block": "Checked",
            "row_reviews": [{
                "scenario_id": scenario_id,
                "test_node_id": test_node,
                "assessment": "SUPPORTED",
                "evidence": "Inspect mapped test and assert lines",
                "when_proofs": when,
                "outcome_proofs": [3, 4],
            }],
        })

    adapter = _PromptCaptureAdapter([answer([2, 3, 4]), answer([2])])

    def validate(parsed: BaseModel) -> None:
        checked = ScenarioCoverageVerifyModelAnswer.model_validate(parsed)
        row = ScenarioCoverageRowReview.model_validate(checked.row_reviews[0])
        problem = review_proof_error(
            tmp_path, "demo", row, expected_node=test_node,
        )
        if problem:
            raise ValueError(f"SCENARIO_ROW_PROOF_INVALID: {problem}")

    result = await adapter.run(
        {"repository_path": str(tmp_path)}, prompt="Check mapped scenario",
        tools=(), payload_model=ScenarioCoverageVerifyModelAnswer,
        max_validation_attempts=2, post_parse_validate=validate,
    )
    assert result.validation_attempts == 2
    assert "expected=1, got=3" in adapter.prompts[1]
    assert result.parsed.row_reviews[0].when_proofs == [2]
    assert adapter.resume_ids == [None, "agent-unchanged"]


def test_delta_prompt_specifies_exact_frozen_proof_counts() -> None:
    from agents.verify.scenario_coverage_verify.prompt.get_prompt import get_delta_prompt

    prompt = get_delta_prompt(
        repository_path="/r",
        change_id="demo",
        plan_evidence_id="plan",
        source_snapshot={"source_digest": "hash"},
        evidence_ledger=[{
            "scenario_id": "spec.md::entry",
            "test_node_id": "tests/test_entry.py::test_entry",
            "when_clauses": ["trigger"],
            "outcome_clauses": ["state", "isolation"],
        }],
    )
    assert "WHEN=1; THEN/AND=2" in prompt
    assert "when_proofs=[одна строка]" in prompt
    assert "Массивы ПОЗИЦИОННЫЕ" in prompt


@pytest.mark.asyncio
async def test_nonretryable_identity_error_never_resends_prompt() -> None:
    from agents.abc_agent import AgentValidationTerminalError

    adapter = _PromptCaptureAdapter(['{"when_proofs":[25]}'])

    def reject_identity(_parsed: BaseModel) -> None:
        raise AgentValidationTerminalError("SCENARIO_ROW_STALE")

    with pytest.raises(AgentValidationTerminalError, match="SCENARIO_ROW_STALE"):
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit source", tools=(),
            payload_model=_ProofAnswer, post_parse_validate=reject_identity,
            max_validation_attempts=4,
        )
    assert len(adapter.prompts) == 1
    assert adapter.resume_ids == [None]


class _TwoRequired(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int
    b: int


@pytest.mark.asyncio
async def test_validation_strict_subset_is_proven_progress_before_pass() -> None:
    """Two missing fields -> one missing field -> correct result is monotonic."""
    adapter = _PromptCaptureAdapter(['{}', '{"a":1}', '{"a":1,"b":2}'])
    result = await adapter.run(
        {"repository_path": "/r"}, prompt="Audit", tools=(),
        payload_model=_TwoRequired, max_validation_attempts=4,
    )
    assert result.validation_attempts == 3
    assert result.parsed.a == 1 and result.parsed.b == 2
    assert adapter.resume_ids == [None, "agent-unchanged", "agent-unchanged"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [
    ['{"a":1}', '{"a":1}'],
    ['{}', '{}'],
])
async def test_validation_schema_repeated_findings_stops(answers) -> None:
    adapter = _PromptCaptureAdapter(answers)
    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_TwoRequired, max_validation_attempts=5,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert error.value.attempt == 2
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
async def test_validation_schema_changed_findings_allows_retry() -> None:
    adapter = _PromptCaptureAdapter(['{"a":1}', '{"a":1,"b":2}'])
    result = await adapter.run(
        {"repository_path": "/r"}, prompt="Audit", tools=(),
        payload_model=_TwoRequired, max_validation_attempts=4,
    )
    assert result.validation_attempts == 2
    assert result.parsed.a == 1 and result.parsed.b == 2


@pytest.mark.asyncio
async def test_prose_plus_valid_json_succeeds_on_first_attempt() -> None:
    adapter = _PromptCaptureAdapter(['Structural audit done.\n\n{"ok": true}'])
    result = await adapter.run(
        {"repository_path": "/r"},
        prompt="Audit",
        tools=(),
        payload_model=_Answer,
        max_validation_attempts=4,
    )
    assert result.validation_attempts == 1
    assert result.parsed is not None and result.parsed.ok is True
    assert len(adapter.prompts) == 1


@pytest.mark.asyncio
async def test_validation_json_to_schema_to_pass_converges() -> None:
    adapter = _PromptCaptureAdapter(['{"a":', '{"a":1}', '{"a":1,"b":2}'])
    result = await adapter.run(
        {"repository_path": "/r"}, prompt="Audit", tools=(),
        payload_model=_TwoRequired, max_validation_attempts=4,
    )
    assert result.validation_attempts == 3
    assert result.parsed.a == 1 and result.parsed.b == 2
    assert adapter.resume_ids == [None, "agent-unchanged", "agent-unchanged"]


@pytest.mark.asyncio
async def test_validation_schema_to_broken_json_is_regression() -> None:
    adapter = _PromptCaptureAdapter(['{"a":1}', '{"a":'])
    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_TwoRequired, max_validation_attempts=4,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
async def test_validation_repeated_unparseable_json_is_not_progress() -> None:
    adapter = _PromptCaptureAdapter(['{"a":', '{"b":'])
    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_TwoRequired, max_validation_attempts=6,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("errors", [
    ["SCENARIO_ROW_PROOF_INVALID: PROOF_CLAUSES_MISMATCH: demo WHEN; expected=1, got=3",
     "SCENARIO_ROW_PROOF_INVALID: PROOF_CLAUSES_MISMATCH: demo WHEN; expected=1, got=2"],
    ["SCENARIO_ROW_PROOF_INVALID: PROOF_NOT_ASSERTION: tests/test_demo.py:30",
     "SCENARIO_ROW_PROOF_INVALID: PROOF_NOT_ASSERTION: tests/test_demo.py:31"],
])
async def test_validation_semantic_repeated_causal_finding_fails_closed(errors) -> None:
    adapter = _PromptCaptureAdapter(['{"when_proofs":[3]}', '{"when_proofs":[2]}'])

    def validator(_parsed: BaseModel) -> None:
        raise ValueError(errors[len(adapter.prompts) - 1])

    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_ProofAnswer, post_parse_validate=validator,
            max_validation_attempts=5,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert error.value.run_id == "run-2"
    assert error.value.agent_id == "agent-unchanged"
    assert error.value.attempt == 2
    assert adapter.resume_ids == [None, "agent-unchanged"]


_START_COVER_NODE = (
    "tests/scenarios/visual/test_19b93ac04b1bb2cf984b4fc6e72422aebe6daef6d61f096509ea9243da612f9a.py"
    "::test_start_cover_resolves_to_slide_one_asset"
)


@pytest.mark.asyncio
async def test_validation_changed_proof_finding_allows_retry_before_limit(
    tmp_path: Path,
) -> None:
    """Смена PROOF_* finding при attempt < limit не даёт VALIDATION_NO_PROGRESS."""
    from agents.verify.scenario_coverage_verify.evidence_contract import review_proof_error
    from agents.verify.scenario_coverage_verify.schemas import (
        ScenarioCoverageRowReview,
        ScenarioCoverageVerifyModelAnswer,
    )

    spec = tmp_path / "openspec/changes/visual/specs/presentation/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### Requirement: cover\n"
        "#### Scenario: Start cover resolves to slide 1 asset\n"
        "- **WHEN** the user is on the start slide\n"
        "- **THEN** the cover filename is resolved\n"
        "- **AND** the asset bytes are checked\n",
        encoding="utf-8",
    )
    test_rel = _START_COVER_NODE.split("::", 1)[0]
    test_file = tmp_path / test_rel
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "def test_start_cover_resolves_to_slide_one_asset():\n"
        "    name = 'cover_start.png'\n"
        "    assert name == 'cover_start.png'\n"
        "    digest = 'abc'\n"
        "    assert digest == 'abc'\n",
        encoding="utf-8",
    )
    scenario_id = "openspec/changes/visual/specs/presentation/spec.md::Start cover resolves to slide 1 asset"

    def answer(when: list[int], outcome: list[int]) -> str:
        return json.dumps({
            "verdict": "SCENARIO_TEST_COVERAGE_PASS",
            "summary": "audit",
            "local_hop_copy_block": "audit",
            "row_reviews": [{
                "scenario_id": scenario_id,
                "test_node_id": _START_COVER_NODE,
                "assessment": "SUPPORTED",
                "evidence": "structural",
                "when_proofs": when,
                "outcome_proofs": outcome,
            }],
        })

    adapter = _PromptCaptureAdapter([
        answer([2], [2, 3]),
        answer([3], [999, 4]),
        answer([3], [3, 5]),
    ])

    def validate(parsed: BaseModel) -> None:
        checked = ScenarioCoverageVerifyModelAnswer.model_validate(parsed)
        row = ScenarioCoverageRowReview.model_validate(checked.row_reviews[0])
        problem = review_proof_error(
            tmp_path, "visual", row, expected_node=_START_COVER_NODE,
        )
        if problem:
            raise ValueError(f"SCENARIO_ROW_PROOF_INVALID: {problem}")

    result = await adapter.run(
        {"repository_path": str(tmp_path)}, prompt="Audit row",
        tools=(), payload_model=ScenarioCoverageVerifyModelAnswer,
        max_validation_attempts=4, post_parse_validate=validate,
    )
    assert result.validation_attempts == 3
    assert result.parsed.row_reviews[0].outcome_proofs == [3, 5]
    assert len(adapter.prompts) == 3
    assert "valid_then_and_assert_lines=" in adapter.prompts[1]


@pytest.mark.asyncio
async def test_validation_repeated_proof_finding_frozenset_stops() -> None:
    adapter = _PromptCaptureAdapter([
        '{"when_proofs":[2]}',
        '{"when_proofs":[3]}',
    ])

    def validator(_parsed: BaseModel) -> None:
        raise ValueError(
            "SCENARIO_ROW_PROOF_INVALID: PROOF_OUTSIDE_TEST: tests/demo.py:99"
        )

    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_ProofAnswer, post_parse_validate=validator,
            max_validation_attempts=4,
        )
    assert error.value.code == "VALIDATION_NO_PROGRESS"
    assert error.value.attempt == 2


@pytest.mark.asyncio
async def test_validation_max_attempts_remains_safety_bound_not_progress_claim() -> None:
    adapter = _PromptCaptureAdapter(['{"a":1}'])
    with pytest.raises(AgentRunContractError) as error:
        await adapter.run(
            {"repository_path": "/r"}, prompt="Audit", tools=(),
            payload_model=_TwoRequired, max_validation_attempts=1,
        )
    assert error.value.code == "SCHEMA_VALIDATION_FAILED"
    assert error.value.attempt == 1
    assert len(adapter.prompts) == 1


def test_validation_progress_predicate_does_not_accept_false_progress() -> None:
    from agents.abc_agent import _validation_causal_findings, _validation_converges

    same_a = _validation_causal_findings(ValueError(
        "SCENARIO_ROW_PROOF_INVALID: PROOF_CLAUSES_MISMATCH: x WHEN; expected=1, got=5"
    ))
    same_b = _validation_causal_findings(ValueError(
        "SCENARIO_ROW_PROOF_INVALID: PROOF_CLAUSES_MISMATCH: x WHEN; expected=1, got=2"
    ))
    new_sibling = _validation_causal_findings(ValueError(
        "SCENARIO_ROW_PROOF_INVALID: PROOF_CLAUSES_MISMATCH: x THEN; expected=1, got=2"
    ))
    assert same_a == same_b
    assert not _validation_converges(same_a, same_b)
    assert _validation_converges(same_a, new_sibling)
    assert not _validation_converges((1, frozenset({"proof"})), (2, frozenset({"schema"})))
    assert _validation_converges((3, frozenset({"json"})), (2, frozenset({"schema"})))
    assert _validation_converges((2, frozenset({"a", "b"})), (2, frozenset({"b"})))
