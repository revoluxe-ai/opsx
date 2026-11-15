from cursor_adapter import (
    MockCursorAdapter,
    MockScenario,
    MockScenarioStep,
    build_bug_batch_clear_output,
    build_clean_output,
    build_five_findings_output,
    build_scenario,
)
from config import (
    MAX_REPAIR_ROUNDS,
    MAX_REVIEW_RUNS,
    REPAIR_PROMPT,
    REVIEW_PROMPT,
)
from loop_engine import BugbotRepairLoop
from parsers import parse_bugbot_result, parse_opsx_bug_fix_result


def _build_repair_limit_scenario(
    name: str,
    repair_count: int,
    final_review: str,
) -> MockScenario:
    steps: list[MockScenarioStep] = []
    for round_idx in range(repair_count):
        review = build_five_findings_output().replace(
            "src/a.js",
            f"src/a{round_idx}.js",
        )
        steps.extend(
            [
                MockScenarioStep(REVIEW_PROMPT, review),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
            ]
        )
    steps.append(MockScenarioStep(REVIEW_PROMPT, final_review))
    return MockScenario(name=name, steps=steps)


def test_parse_bugbot_findings_and_fingerprint():
    raw = build_five_findings_output()
    parsed = parse_bugbot_result(raw)
    assert parsed.completed is True
    assert parsed.clean is False
    assert len(parsed.actionable_findings) == 5
    assert parsed.fingerprint is not None
    assert parse_bugbot_result(build_clean_output()).clean is True


def test_parse_opsx_bug_fix_terminal():
    parsed = parse_opsx_bug_fix_result(build_bug_batch_clear_output())
    assert parsed.completed is True
    assert parsed.bug_batch_clear is True
    assert parsed.running_worker == "none"


def test_fingerprint_stable_for_same_findings():
    raw = build_five_findings_output()
    first = parse_bugbot_result(raw).fingerprint
    second = parse_bugbot_result(raw.replace("5 findings", "5 findings again")).fingerprint
    assert first == second


def test_scenario1_mock_loop():
    outcome = BugbotRepairLoop(MockCursorAdapter(build_scenario("scenario1"))).run()
    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == 2
    assert outcome.review_runs == 3


def test_repair_budget_allows_clean_review_before_limit():
    outcome = BugbotRepairLoop(
        MockCursorAdapter(
            _build_repair_limit_scenario(
                "clean-before-limit",
                MAX_REPAIR_ROUNDS - 1,
                build_clean_output(),
            )
        )
    ).run()

    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == MAX_REPAIR_ROUNDS - 1
    assert outcome.review_runs == MAX_REPAIR_ROUNDS


def test_scenario2_max_rounds_reached():
    adapter = MockCursorAdapter(build_scenario("scenario2"))
    outcome = BugbotRepairLoop(adapter).run()

    assert outcome.status == "MAX_ROUNDS_REACHED"
    assert outcome.repair_rounds == MAX_REPAIR_ROUNDS
    assert outcome.review_runs == MAX_REVIEW_RUNS
    assert len(outcome.repair_history) == MAX_REPAIR_ROUNDS
    assert len(outcome.review_history) == MAX_REVIEW_RUNS
    assert outcome.last_repair is not None
    assert outcome.last_repair.repaired_this_batch is True
    assert adapter.history[-1].prompt == REVIEW_PROMPT
    assert sum(item.prompt == REPAIR_PROMPT for item in adapter.history) == MAX_REPAIR_ROUNDS


def test_tenth_repair_is_followed_by_clean_final_review():
    adapter = MockCursorAdapter(
        _build_repair_limit_scenario(
            "clean-after-tenth-repair",
            MAX_REPAIR_ROUNDS,
            build_clean_output(),
        )
    )
    outcome = BugbotRepairLoop(adapter).run()

    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == MAX_REPAIR_ROUNDS
    assert outcome.review_runs == MAX_REVIEW_RUNS
    assert len(outcome.repair_history) == MAX_REPAIR_ROUNDS
    assert len(outcome.review_history) == MAX_REVIEW_RUNS
    assert outcome.last_repair is not None
    assert outcome.last_repair.repaired_this_batch is True
    assert adapter.history[-1].prompt == REVIEW_PROMPT
    assert sum(item.prompt == REPAIR_PROMPT for item in adapter.history) == MAX_REPAIR_ROUNDS


def test_scenario3_stalled():
    outcome = BugbotRepairLoop(MockCursorAdapter(build_scenario("scenario3"))).run()
    assert outcome.status == "STALLED"
    assert outcome.repair_rounds == 2
    assert outcome.review_runs == 3


def test_scenario4_technical_retry_does_not_add_repair_round():
    adapter = MockCursorAdapter(build_scenario("scenario4"))
    outcome = BugbotRepairLoop(adapter).run()
    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == 1
    assert outcome.review_runs == 2
    review_calls = [item for item in adapter.history if item.prompt.startswith("/review-bugbot")]
    assert len(review_calls) == 3
    assert review_calls[0].exit_code == 1
    assert review_calls[1].exit_code == 0
    assert review_calls[2].exit_code == 0
