from config import REPAIR_PROMPT, REVIEW_PROMPT
from cursor_adapter import PromptInvocation, build_bug_batch_clear_output, build_clean_output
from loop_engine import BugbotRepairLoop
from parsers import parse_bugbot_result


def test_repair_prompt_keeps_orch_slash_and_green_contract():
    assert REPAIR_PROMPT.startswith("/opsx-orch-bug-fix")
    assert "node --test" in REPAIR_PROMPT
    assert "BLOCKED_WORKER_STATE" in REPAIR_PROMPT


class RecordingAdapter:
    def __init__(self, review_outputs: list[str]) -> None:
        self._session_id = "session-0"
        self._review_outputs = list(review_outputs)
        self._review_index = 0
        self.reset_reasons: list[str] = []
        self.history: list[PromptInvocation] = []

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        self.reset_reasons.append(reason or "reset")
        self._session_id = f"session-{len(self.reset_reasons)}"
        return self._session_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        if prompt == REVIEW_PROMPT:
            raw = self._review_outputs[self._review_index]
            self._review_index += 1
        elif prompt == REPAIR_PROMPT:
            raw = build_bug_batch_clear_output()
        elif prompt.startswith("/opsx-commit-all"):
            raw = "STATUS: COMPLETE_NOOP\n"
        else:
            raise AssertionError(f"unexpected prompt: {prompt!r}")

        invocation = PromptInvocation(
            prompt=prompt,
            raw_output=raw,
            exit_code=0,
            session_id=self._session_id,
        )
        self.history.append(invocation)
        return invocation


def test_degraded_review_retries_in_fresh_session():
    degraded = """Shell is blocked by a broken hook, so this is a working-tree review.

Bugbot found 1 findings
| High | src/a.js:1 | Example |
"""
    adapter = RecordingAdapter([degraded, build_clean_output()])
    outcome = BugbotRepairLoop(adapter).run()

    assert outcome.status == "BUGBOT_CLEAN"
    assert adapter.reset_reasons == ["review-retry-1"]
    assert adapter.history[0].session_id == "session-0"
    assert adapter.history[1].session_id == "session-1"
    assert parse_bugbot_result(degraded).completed is False


def test_post_repair_review_uses_fresh_session():
    findings = """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 1

Bugbot found 1 findings
| High | src/a.js:1 | Example |
"""
    adapter = RecordingAdapter([findings, build_clean_output()])
    outcome = BugbotRepairLoop(adapter).run()

    assert outcome.status == "BUGBOT_CLEAN"
    assert adapter.reset_reasons == ["post-repair-round"]
    review_sessions = [item.session_id for item in adapter.history if item.prompt == REVIEW_PROMPT]
    assert review_sessions == ["session-0", "session-1"]
