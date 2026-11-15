"""Security lane: `/review-security` + `/opsx-bug-team` on product-lane findings."""

from config import (
    REPAIR_PROMPT,
    SECURITY_REPAIR_PROMPT,
    SECURITY_REVIEW_ARTIFACT_FALLBACK_PROMPT,
    SECURITY_REVIEW_PROMPT,
)
from cursor_adapter import (
    MockCursorAdapter,
    PromptInvocation,
    build_scenario,
    build_security_batch_clear_output,
    build_security_clean_output,
    build_security_findings_output,
)
from lanes import SECURITY_LANE, SECURITY_LANE_ID, get_lane, make_security_lane
from loop_engine import ReviewRepairLoop
from parsers import (
    is_security_preflight_blocker_only,
    parse_security_repair_result,
    parse_security_review_result,
)

def test_security_prompts_use_bug_team_not_orch():
    assert SECURITY_REVIEW_PROMPT.startswith("/review-security")
    assert "{base}" in SECURITY_REVIEW_PROMPT
    assert "REVIEW_SCOPE: SRC_TESTS_ONLY" in SECURITY_REVIEW_PROMPT
    assert "SRC_TESTS_SCOPE" in SECURITY_REVIEW_PROMPT
    assert "`src/**` and `tests/**`" in SECURITY_REVIEW_PROMPT
    assert "subagent_type: \"security-review\"" in SECURITY_REVIEW_PROMPT
    assert "POC_GIT_PREFLIGHT: VERIFIED" in SECURITY_REVIEW_PROMPT
    assert "PREFLIGHT_ARTIFACT" in SECURITY_REVIEW_PROMPT
    assert "FIRST action: Read PREFLIGHT_ARTIFACT" in SECURITY_REVIEW_PROMPT
    assert "default to `origin/HEAD`" in SECURITY_REVIEW_PROMPT
    assert "FALLBACK (required" in SECURITY_REVIEW_PROMPT
    assert "source of truth" in SECURITY_REVIEW_PROMPT
    assert "Emitting only the" in SECURITY_REVIEW_PROMPT
    assert "subagent blocker text is FORBIDDEN" in SECURITY_REVIEW_PROMPT
    assert "/opsx-security-review" in SECURITY_REVIEW_PROMPT
    assert "not `/opsx-security-review`" in SECURITY_REVIEW_PROMPT
    assert "/opsx-orch-bug-fix" not in SECURITY_REVIEW_PROMPT
    assert SECURITY_REPAIR_PROMPT.startswith("/opsx-bug-team")
    assert SECURITY_REPAIR_PROMPT != REPAIR_PROMPT
    assert "Intake only `src/**` and `tests/**` rows" in SECURITY_REPAIR_PROMPT
    assert "Do not run `/opsx-orch-bug-fix`" in SECURITY_REPAIR_PROMPT
    assert "/opsx-security-fix" in SECURITY_REPAIR_PROMPT


def test_make_security_lane_base_in_prompt():
    lane = make_security_lane(base="main")
    assert "Base Branch: main" in lane.review_prompt
    assert lane.repair_prompt == SECURITY_REPAIR_PROMPT


def test_security_artifact_fallback_prompt_template():
    rendered = SECURITY_REVIEW_ARTIFACT_FALLBACK_PROMPT.format(
        artifact_path=".tmp/temporal/git-preflight-latest.md",
        base="stage",
    )
    assert "ARTIFACT-ONLY FALLBACK" in rendered
    assert "PREFLIGHT_ARTIFACT" in rendered
    assert "Security review found no issues" in rendered
    assert "could not compute the branch-changes diff" in rendered


def test_is_security_preflight_blocker_only():
    blocker = (
        "Security review could not complete: the review subagent "
        "could not compute the branch-changes diff."
    )
    assert is_security_preflight_blocker_only(blocker) is True
    fallback = """The security-review subagent could not compute the branch-changes diff.
FALLBACK: re-read PREFLIGHT_ARTIFACT and summarize.
Security review found no issues"""
    assert is_security_preflight_blocker_only(fallback) is False
    assert is_security_preflight_blocker_only(build_security_clean_output()) is False


def test_parse_security_review_without_scope_header():
    parsed = parse_security_review_result(build_security_findings_output())
    assert parsed.completed is True
    assert parsed.clean is False
    assert len(parsed.repairable_findings) == 2


def test_parse_security_clean_and_empty_diff():
    clean = parse_security_review_result(build_security_clean_output())
    assert clean.completed is True
    assert clean.clean is True
    empty = parse_security_review_result("there was no diff to review")
    assert empty.completed is True
    assert empty.clean is True


def test_parse_security_process_only_is_product_clean():
    raw = """Security review found 1 findings

| Severity | Location (file:line) | Finding |
| High | opsx/tools/lib/a.mjs:10 | Process secret logging |
"""
    parsed = parse_security_review_result(raw)
    assert parsed.completed is True
    assert parsed.clean is True
    assert parsed.repairable_findings == []
    assert len(parsed.excluded_findings) == 1


def test_parse_security_subagent_diff_failure_is_not_clean():
    raw = """I'll run a security review of this branch's changes against the default base.
The first review pass failed to compute the branch diff. Retrying once with the same prompt.
Security review could not complete: the review subagent could not compute the branch-changes diff in `C:\\Users\\vkita\\my_code\\game`."""
    parsed = parse_security_review_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.repairable_findings == []


def test_parse_security_preflight_fallback_clean_after_subagent_diff_failure():
    """When POC preflight verified, parent FALLBACK summary must parse as clean."""
    raw = """The security-review subagent could not compute the branch-changes diff.
FALLBACK: re-read PREFLIGHT_ARTIFACT (SRC_TESTS_ONLY vs stage) and summarize from it.
Security review found no issues"""
    parsed = parse_security_review_result(raw, git_preflight_ok=True)
    assert parsed.completed is True
    assert parsed.clean is True
    assert parsed.repairable_findings == []


def test_parse_security_preflight_ok_blocker_only_still_incomplete():
    """Verified preflight does not turn a pure subagent blocker into SECURITY_CLEAN."""
    raw = """Security review could not complete: the review subagent could not compute the branch-changes diff."""
    parsed = parse_security_review_result(raw, git_preflight_ok=True)
    assert parsed.completed is False
    assert parsed.clean is None


def test_parse_security_repair_batch_clear():
    parsed = parse_security_repair_result(build_security_batch_clear_output())
    assert parsed.completed is True
    assert parsed.bug_batch_clear is True
    assert parsed.uncertain is False


def test_get_lane_security_default_and_base():
    lane = get_lane("security")
    assert lane.id == "security"
    assert lane.git_preflight is True
    assert lane.git_preflight_path_prefixes == ("src/", "tests/")
    assert lane.clean_status == "SECURITY_CLEAN"
    assert "Base Branch: stage" in lane.review_prompt
    assert "SRC_TESTS_SCOPE" in lane.review_prompt

    main_lane = get_lane("security", base="main")
    assert "Base Branch: main" in main_lane.review_prompt


def test_security_lane_collects_scoped_git_preflight(monkeypatch):
    lane = make_security_lane()
    captured: dict[str, object] = {}

    class RepoAdapter:
        repo_path = "C:/Users/vkita/my_code/game"

        def session_id(self) -> str:
            return "session-0"

        def reset_session(self, *, reason: str | None = None) -> str:
            return self.session_id()

        def execute_prompt(self, prompt: str, *, step: str | None = None):
            raise AssertionError("not called")

    def fake_collect(repo_path, *, base_ref, path_prefixes=None):
        captured["repo_path"] = repo_path
        captured["base_ref"] = base_ref
        captured["path_prefixes"] = path_prefixes
        from git_preflight import GitPreflightResult

        return GitPreflightResult(
            ok=True,
            base_ref=base_ref,
            head_ref="head-sha",
            merge_base="merge-base-sha",
            diff_stat=" src/a.js | 1 +\n",
            diff_stat_lines=1,
            changed_files=("src/a.js",),
            file_diffs=(("src/a.js", "+x"),),
            review_scope="SRC_TESTS_ONLY",
            path_prefixes=path_prefixes or (),
        )

    monkeypatch.setattr("loop_engine.collect_git_preflight", fake_collect)
    prompt, git_ok, error = ReviewRepairLoop(RepoAdapter(), lane=lane)._resolve_review_context()
    assert captured["path_prefixes"] == ("src/", "tests/")
    assert git_ok is True
    assert error is None
    assert "POC_GIT_PREFLIGHT: VERIFIED" in prompt
    assert "REVIEW_SCOPE: SRC_TESTS_ONLY" in prompt
    assert "PREFLIGHT_ARTIFACT" in prompt
    assert "FALLBACK" in prompt
    assert "FIRST action: Read PREFLIGHT_ARTIFACT" in prompt


def test_security_scenario1_mock_loop():
    outcome = ReviewRepairLoop(
        MockCursorAdapter(build_scenario("security-scenario1")),
        lane=SECURITY_LANE,
    ).run()
    assert outcome.status == "SECURITY_CLEAN"
    assert outcome.repair_rounds == 2
    assert outcome.review_runs == 3


class SecurityFallbackRecordingAdapter:
    """Records review prompts; first review returns blocker-only, fallback returns clean."""

    repo_path = "C:/Users/vkita/my_code/game"

    def __init__(self, *, blocker: str, fallback_clean: str) -> None:
        self._session_id = "session-0"
        self.reset_reasons: list[str] = []
        self.prompts: list[str] = []
        self._blocker = blocker
        self._fallback_clean = fallback_clean

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        self.reset_reasons.append(reason or "reset")
        self._session_id = f"session-{len(self.reset_reasons)}"
        return self._session_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        self.prompts.append(prompt)
        if step == "review-fallback":
            raw = self._fallback_clean
        elif step == "review":
            raw = self._blocker
        else:
            raise AssertionError(f"unexpected step: {step!r}")

        return PromptInvocation(
            prompt=prompt,
            raw_output=raw,
            exit_code=0,
            session_id=self._session_id,
        )


def test_security_loop_retries_artifact_fallback_on_blocker_only(monkeypatch):
    blocker = (
        "Security review could not complete: the review subagent "
        "could not compute the branch-changes diff."
    )
    adapter = SecurityFallbackRecordingAdapter(
        blocker=blocker,
        fallback_clean=build_security_clean_output(),
    )

    def fake_collect(repo_path, *, base_ref, path_prefixes=None):
        from git_preflight import GitPreflightResult

        return GitPreflightResult(
            ok=True,
            base_ref=base_ref,
            head_ref="head-sha",
            merge_base="merge-base-sha",
            diff_stat=" src/a.js | 1 +\n",
            diff_stat_lines=1,
            changed_files=("src/a.js",),
            file_diffs=(("src/a.js", "+x"),),
            review_scope="SRC_TESTS_ONLY",
            path_prefixes=path_prefixes or (),
        )

    monkeypatch.setattr("loop_engine.collect_git_preflight", fake_collect)
    outcome = ReviewRepairLoop(adapter, lane=make_security_lane()).run()

    assert outcome.status == "SECURITY_CLEAN"
    assert outcome.review_runs == 1
    assert adapter.reset_reasons == ["security-artifact-fallback"]
    assert len(adapter.prompts) == 2
    assert "ARTIFACT-ONLY FALLBACK" in adapter.prompts[1]
    assert SECURITY_LANE_ID == "security"
