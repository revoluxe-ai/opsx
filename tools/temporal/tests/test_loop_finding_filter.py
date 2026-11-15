from config import REVIEW_PROMPT
from cursor_adapter import PromptInvocation, build_clean_output
from loop_engine import BugbotRepairLoop


class OpsxOnlyReviewAdapter:
    def __init__(self, raw_output: str) -> None:
        self._session_id = "session-0"
        self._raw_output = raw_output
        self.history: list[PromptInvocation] = []

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        return self._session_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        if prompt != REVIEW_PROMPT:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        invocation = PromptInvocation(
            prompt=prompt,
            raw_output=self._raw_output,
            exit_code=0,
            session_id=self._session_id,
        )
        self.history.append(invocation)
        return invocation


def test_only_opsx_findings_stop_without_repair():
    raw = """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 2

Bugbot found 2 findings

| Severity | Location | Finding |
| High | opsx/tools/lib/a.mjs:10 | Process bug |
| Medium | opsx/tools/lib/b.mjs:20 | Process bug |
"""
    outcome = BugbotRepairLoop(OpsxOnlyReviewAdapter(raw)).run()
    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == 0
    assert outcome.review_runs == 1
    assert len(outcome.review_history[0].excluded_findings) == 2
    assert outcome.review_history[0].repairable_findings == []
    assert "process finding(s) excluded" in outcome.message


def test_true_clean_still_stops_without_repair():
    outcome = BugbotRepairLoop(OpsxOnlyReviewAdapter(build_clean_output())).run()
    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == 0


def test_temporal_poc_findings_stop_without_repair():
    raw = """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 2

Bugbot found 2 findings

| Severity | Location (file:line) | Finding |
| High | tools/temporal/git_preflight.py:110 | File-diff cap is 25 |
| Medium | tools/temporal/git_preflight.py:88 | Preflight uses stage...HEAD only |
"""
    outcome = BugbotRepairLoop(OpsxOnlyReviewAdapter(raw)).run()
    assert outcome.status == "BUGBOT_CLEAN"
    assert outcome.repair_rounds == 0
    assert outcome.review_runs == 1
    assert outcome.review_history[0].repairable_findings == []
    assert len(outcome.review_history[0].excluded_findings) == 2
