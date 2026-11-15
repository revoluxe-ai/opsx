"""Post-repair /opsx-commit-all GO materialization for all adversarial lanes."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_bugbot_review,
    execute_opsx_repair,
)
from commit_materialization import (
    COMMIT_MATERIALIZE_BLOCKED,
    current_head,
    is_product_tree_clean,
    is_tree_clean,
    materialize_if_needed,
    parse_commit_all_status,
)
from config import COMMIT_ALL_PROMPT, commit_all_prompt_for_materialize
from config import MAX_REPAIR_ROUNDS, MAX_REVIEW_RUNS, TEMPORAL_TASK_QUEUE
from cursor_adapter import PromptInvocation
from lanes import make_project_bug_hunt_lane, make_security_lane
from loop_engine import BugbotRepairLoop, ReviewRepairLoop
from models import RepairRunResult
from workflow import BugbotRepairWorkflow


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "src" / "app.js").write_text("BUG\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "app.test.mjs").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "candidate-A")
    _git(repo, "branch", "stage")
    sha_a = current_head(repo)
    assert sha_a
    return repo, sha_a


def _repair_yes() -> str:
    return (
        "STATUS: BUG_BATCH_CLEAR\n"
        "BUG_BATCH_CLEAR: YES\n"
        "REPAIRED_THIS_BATCH: YES\n"
        "RUNNING_WORKER: none\n"
        "UNRESOLVED:\n  none\n"
    )


def _repair_no() -> str:
    return (
        "STATUS: PARTIAL_COMPLETE\n"
        "REPAIRED_THIS_BATCH: NO\n"
        "OWNER_AGGREGATE:\n"
        "  FIX_NOW: (none)\n"
        "RUNNING_WORKER: none\n"
        "UNRESOLVED:\n  none\n"
    )


def _repair_misleading_no() -> str:
    return (
        "STATUS: BUG_BATCH_CLEAR\n"
        "BUG_BATCH_CLEAR: YES\n"
        "REPAIRED_THIS_BATCH: NO\n"
        "OWNER_AGGREGATE:\n"
        "  FIX_NOW: none\n"
        "RUNNING_WORKER: none\n"
        "UNRESOLVED:\n  none\n"
    )


def _bugbot_findings() -> str:
    return (
        "REVIEW_SCOPE: FULL_BRANCH_DIFF\n"
        "BASE_REF: stage\n"
        "HEAD_REF: feature\n"
        "DIFF_STAT_LINES: 1\n\n"
        "Bugbot found 1 findings\n"
        "| High | src/app.js:1 | leftover BUG token |\n"
    )


def _bugbot_clean() -> str:
    return (
        "REVIEW_SCOPE: FULL_BRANCH_DIFF\n"
        "BASE_REF: stage\n"
        "HEAD_REF: feature\n"
        "DIFF_STAT_LINES: 0\n\n"
        "Bugbot found no bugs.\n"
    )


def _security_findings() -> str:
    return (
        "Security review found 1 findings\n\n"
        "| Severity | Location (file:line) | Finding |\n"
        "| High | src/app.js:1 | leftover BUG token |\n"
    )


def _security_clean() -> str:
    return "Security review found no issues"


def _security_repair() -> str:
    return "STATUS: SECURITY_BATCH_CLEAR\nSECURITY_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"


def _hunt_findings() -> str:
    return (
        "STATUS: HUNT_COMPLETE\n"
        "PROJECT_BUG_HUNT_RECEIPT:\n"
        "- findings_total: 1\n"
        "- confirmed_production_bug_count: 1\n"
        "FINDINGS:\n"
        "### FINDING_ID: PBH-001\n"
        "- TITLE: leftover BUG token\n"
        "- LOC_LINK: src/app.js:1\n"
        "- LITERAL_CLAIM: BUG remains in committed candidate\n"
        "- PRODUCTION_CLASSIFICATION: High\n"
        "- SEVERITY: High\n"
    )


def _hunt_clean() -> str:
    return (
        "STATUS: HUNT_COMPLETE\n"
        "PROJECT_BUG_HUNT_RECEIPT:\n"
        "- findings_total: 0\n"
        "- confirmed_production_bug_count: 0\n"
        "FINDINGS:\n"
    )


class MaterializingAdapter:
    """Simulates Cursor hops; `/opsx-commit-all GO` actually commits in the test repo."""

    def __init__(
        self,
        repo: Path,
        *,
        finding_output: str,
        clean_output: str,
        repair_output: str,
        mutate,
        commit_mode: str = "complete",
        max_findings: int = 1,
        review_outputs: list[str] | None = None,
        vary_findings: bool = False,
    ) -> None:
        self.repo_path = str(repo)
        self._repo = repo
        self._finding_output = finding_output
        self._clean_output = clean_output
        self._repair_output = repair_output
        self._mutate = mutate
        self._commit_mode = commit_mode
        self._max_findings = max_findings
        self._review_outputs = list(review_outputs) if review_outputs is not None else None
        self._vary_findings = vary_findings
        self._session_id = "session-0"
        self.review_prompts: list[str] = []
        self.review_heads: list[str | None] = []
        self.steps: list[str] = []
        self.commit_all_calls = 0
        self.repair_calls = 0

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        self._session_id = f"{self._session_id}-{reason or 'reset'}"
        return self._session_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        resolved = step or "unknown"
        if resolved == "review" or (resolved == "unknown" and "review" in prompt.lower()):
            resolved = "review"
        if prompt == COMMIT_ALL_PROMPT or resolved == "commit-all":
            return self._commit_all(prompt)
        if resolved == "repair":
            return self._repair(prompt)
        if resolved == "review":
            return self._review(prompt)
        raise AssertionError(f"unexpected step={step!r} prompt={prompt[:80]!r}")

    def _review(self, prompt: str) -> PromptInvocation:
        self.steps.append("review")
        self.review_prompts.append(prompt)
        self.review_heads.append(current_head(self._repo))
        review_n = self.steps.count("review")
        if self._review_outputs is not None:
            raw = self._review_outputs[min(review_n - 1, len(self._review_outputs) - 1)]
        else:
            src = (self._repo / "src" / "app.js").read_text(encoding="utf-8")
            tests = (self._repo / "tests" / "app.test.mjs").read_text(encoding="utf-8")
            if "BUG" in src or "BUG" in tests:
                raw = self._finding_output
            else:
                raw = self._clean_output
            if self._max_findings > 1 and review_n <= self._max_findings:
                raw = self._finding_output
            if self._vary_findings and "Bugbot found" in raw:
                raw = raw.replace("src/app.js:1", f"src/app.js:{review_n}")
        return PromptInvocation(
            prompt=prompt, raw_output=raw, exit_code=0, session_id=self._session_id
        )

    def _repair(self, prompt: str) -> PromptInvocation:
        self.steps.append("repair")
        self.repair_calls += 1
        self._mutate(self._repo)
        return PromptInvocation(
            prompt=prompt,
            raw_output=self._repair_output,
            exit_code=0,
            session_id=self._session_id,
        )

    def _commit_all(self, prompt: str) -> PromptInvocation:
        self.steps.append("commit-all")
        self.commit_all_calls += 1
        if self._commit_mode == "fail":
            raw = "STATUS: BLOCKED\nREASON: simulated commit-all failure\n"
        elif self._commit_mode == "noop":
            raw = "STATUS: COMPLETE_NOOP\n"
        else:
            if not is_tree_clean(self._repo):
                _git(self._repo, "add", "-A")
                _git(self._repo, "commit", "-m", "materialize-repair")
            raw = "STATUS: COMPLETE\n"
        return PromptInvocation(
            prompt=prompt, raw_output=raw, exit_code=0, session_id=self._session_id
        )


def _fix_product(repo: Path) -> None:
    (repo / "src" / "app.js").write_text("FIXED\n", encoding="utf-8")


def _fix_tests(repo: Path) -> None:
    (repo / "tests" / "app.test.mjs").write_text("FIXED\n", encoding="utf-8")
    (repo / "src" / "app.js").write_text("FIXED\n", encoding="utf-8")


def _fix_process_and_product(repo: Path) -> None:
    _fix_product(repo)
    cursor = repo / ".cursor"
    cursor.mkdir(exist_ok=True)
    (cursor / "note.md").write_text("process side effect\n", encoding="utf-8")


def _write_closed_fixed(repo: Path) -> None:
    materialize = repo / "opsx" / "runs" / "bug-cases" / "materialize"
    materialize.mkdir(parents=True)
    (materialize / "closed-WI-1.json").write_text(
        json.dumps({"terminal_status": "closed_fixed"}),
        encoding="utf-8",
    )
    (repo / "src" / "app.js").write_text("FIXED\n", encoding="utf-8")


def _extract_head_ref(prompt: str) -> str | None:
    matches = re.findall(r"HEAD_REF:\s*(\S+)", prompt)
    for value in reversed(matches):
        if value.startswith("<"):
            continue
        return value
    match = re.search(r"\bhead=(\S+)", prompt)
    if match:
        return match.group(1)
    return None


def _run_lane(
    repo: Path,
    *,
    lane_id: str,
    mutate,
    repair_output: str | None = None,
    commit_mode: str = "complete",
    max_findings: int = 1,
    sha_a: str | None = None,
    review_outputs: list[str] | None = None,
    vary_findings: bool = False,
):
    if lane_id == "bugbot":
        adapter = MaterializingAdapter(
            repo,
            finding_output=_bugbot_findings(),
            clean_output=_bugbot_clean(),
            repair_output=repair_output or _repair_yes(),
            mutate=mutate,
            commit_mode=commit_mode,
            max_findings=max_findings,
            review_outputs=review_outputs,
            vary_findings=vary_findings,
        )
        loop = BugbotRepairLoop(adapter, repo_path=str(repo), base_ref="stage")
    elif lane_id == "security":
        adapter = MaterializingAdapter(
            repo,
            finding_output=_security_findings(),
            clean_output=_security_clean(),
            repair_output=repair_output or _security_repair(),
            mutate=mutate,
            commit_mode=commit_mode,
            max_findings=max_findings,
            review_outputs=review_outputs,
            vary_findings=vary_findings,
        )
        loop = ReviewRepairLoop(
            adapter,
            lane=make_security_lane(base="stage"),
            repo_path=str(repo),
            base_ref="stage",
        )
    else:
        adapter = MaterializingAdapter(
            repo,
            finding_output=_hunt_findings(),
            clean_output=_hunt_clean(),
            repair_output=repair_output or _repair_yes(),
            mutate=mutate,
            commit_mode=commit_mode,
            max_findings=max_findings,
            review_outputs=review_outputs,
            vary_findings=vary_findings,
        )
        loop = ReviewRepairLoop(
            adapter,
            lane=make_project_bug_hunt_lane(
                base="stage",
                head=sha_a or "feature",
                change="gacha-auth-balance-bonus",
            ),
            repo_path=str(repo),
            base_ref="stage",
            head=sha_a or "feature",
            change="gacha-auth-balance-bonus",
        )
    outcome = loop.run()
    return outcome, adapter


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_product_mutation_commits_then_review_sees_head_b(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(repo, lane_id=lane_id, mutate=_fix_product, sha_a=sha_a)
    sha_b = current_head(repo)
    assert sha_b and sha_b != sha_a
    assert outcome.last_materialize["tree_clean"] is True
    if lane_id == "project-bug-hunt":
        assert is_tree_clean(repo)
    assert adapter.commit_all_calls == 1
    assert adapter.repair_calls == 1
    assert adapter.review_heads[0] == sha_a
    assert adapter.review_heads[-1] == sha_b
    assert sha_a not in adapter.review_heads[1:]
    assert outcome.candidate_sha == sha_b
    assert outcome.last_materialize["candidate_transitioned"] is True
    assert outcome.status in {"BUGBOT_CLEAN", "SECURITY_CLEAN", "HUNT_CLEAN"}
    if lane_id != "project-bug-hunt":
        assert _extract_head_ref(adapter.review_prompts[-1]) == sha_b
    else:
        assert f"head={sha_b}" in adapter.review_prompts[-1]
        assert f"head={sha_a}" in adapter.review_prompts[0]


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_test_only_repair_commits_head_b(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(repo, lane_id=lane_id, mutate=_fix_tests, sha_a=sha_a)
    sha_b = current_head(repo)
    assert sha_b != sha_a
    assert adapter.review_heads[-1] == sha_b
    assert outcome.status.endswith("_CLEAN") or outcome.status in {
        "BUGBOT_CLEAN",
        "SECURITY_CLEAN",
        "HUNT_CLEAN",
    }


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_process_and_product_dirty_commit_all_cleans_tree(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo, lane_id=lane_id, mutate=_fix_process_and_product, sha_a=sha_a
    )
    assert outcome.last_materialize["tree_clean"] is True
    if lane_id == "project-bug-hunt":
        assert is_tree_clean(repo)
    assert current_head(repo) != sha_a


def test_complete_noop_does_not_fake_candidate_transition(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)

    def _no_op_mutate(_repo: Path) -> None:
        return None

    outcome, adapter = _run_lane(
        repo,
        lane_id="project-bug-hunt",
        mutate=_no_op_mutate,
        commit_mode="noop",
        repair_output=_repair_yes(),
        sha_a=sha_a,
        review_outputs=[_hunt_findings(), _hunt_clean()],
    )
    assert adapter.commit_all_calls == 1
    assert outcome.last_materialize["status"] == "COMPLETE_NOOP"
    assert outcome.last_materialize["candidate_transitioned"] is False
    assert current_head(repo) == sha_a
    assert adapter.review_heads == [sha_a, sha_a]
    assert outcome.status == "HUNT_CLEAN"


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_stale_original_head_never_reused_after_commit(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    _outcome, adapter = _run_lane(repo, lane_id=lane_id, mutate=_fix_product, sha_a=sha_a)
    assert adapter.review_heads[0] == sha_a
    assert all(head != sha_a for head in adapter.review_heads[1:])


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_finding_on_a_disappears_once_fix_is_in_b(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(repo, lane_id=lane_id, mutate=_fix_product, sha_a=sha_a)
    assert "BUG" not in (repo / "src" / "app.js").read_text(encoding="utf-8")
    assert outcome.status in {"BUGBOT_CLEAN", "SECURITY_CLEAN", "HUNT_CLEAN"}
    assert adapter.steps == ["review", "repair", "commit-all", "review"]


def test_closed_fixed_plus_misleading_prose_still_rereviews(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo,
        lane_id="project-bug-hunt",
        mutate=_write_closed_fixed,
        repair_output=_repair_misleading_no(),
        sha_a=sha_a,
        review_outputs=[_hunt_findings(), _hunt_clean()],
    )
    assert adapter.repair_calls == 1
    assert adapter.commit_all_calls == 1
    assert outcome.review_runs == 2
    assert outcome.status == "HUNT_CLEAN"
    assert current_head(repo) != sha_a


def test_true_no_progress_is_no_repair_progress(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)

    def _no_mutate(_repo: Path) -> None:
        return None

    outcome, adapter = _run_lane(
        repo,
        lane_id="bugbot",
        mutate=_no_mutate,
        repair_output=_repair_no(),
        sha_a=sha_a,
    )
    assert outcome.status == "NO_REPAIR_PROGRESS"
    assert adapter.commit_all_calls == 0
    assert adapter.steps == ["review", "repair"]
    assert current_head(repo) == sha_a


def test_tenth_repair_materializes_then_final_review_no_eleventh_repair(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    assert MAX_REPAIR_ROUNDS == 10
    assert MAX_REVIEW_RUNS == 11

    def _keep_bug(repo_path: Path) -> None:
        (repo_path / "src" / "app.js").write_text(
            f"BUG round mutation\n",
            encoding="utf-8",
        )

    outcome, adapter = _run_lane(
        repo,
        lane_id="bugbot",
        mutate=_keep_bug,
        commit_mode="complete",
        max_findings=11,
        vary_findings=True,
        sha_a=sha_a,
    )
    assert adapter.repair_calls == 10
    assert adapter.commit_all_calls == 10
    assert adapter.steps.count("review") == 11
    assert adapter.steps[-1] == "review"
    assert "repair" not in adapter.steps[-2:]
    assert outcome.repair_rounds == 10
    assert outcome.review_runs == 11
    assert outcome.status == "MAX_ROUNDS_REACHED"


@pytest.mark.parametrize("lane_id", ["bugbot", "security", "project-bug-hunt"])
def test_commit_failure_does_not_review_unmaterialized_candidate(tmp_path: Path, lane_id: str):
    repo, sha_a = init_repo(tmp_path)
    outcome, adapter = _run_lane(
        repo,
        lane_id=lane_id,
        mutate=_fix_product,
        commit_mode="fail",
        sha_a=sha_a,
    )
    assert outcome.status == COMMIT_MATERIALIZE_BLOCKED
    assert adapter.steps == ["review", "repair", "commit-all"]
    assert adapter.repair_calls == 1
    assert current_head(repo) == sha_a
    assert not is_tree_clean(repo)
    assert "forbidden" in outcome.message.lower()


def test_parse_commit_all_status_tokens():
    assert parse_commit_all_status("STATUS: COMPLETE\n") == "COMPLETE"
    assert parse_commit_all_status("STATUS: COMPLETE_NOOP\n") == "COMPLETE_NOOP"
    assert parse_commit_all_status("STATUS: BLOCKED\n") == "BLOCKED"
    assert parse_commit_all_status("**PARENT_STATUS:** `PARTIAL`\n") == "PARTIAL"
    assert parse_commit_all_status("prose\nSTATUS: PARTIAL\n") == "PARTIAL"
    assert parse_commit_all_status("STATUS: BLOCKED\nSTATUS: COMPLETE\n") == "COMPLETE"


def test_commit_all_materialize_prompt_includes_parse_trailer():
    prompt = commit_all_prompt_for_materialize()
    assert COMMIT_ALL_PROMPT in prompt
    assert "TEMPORAL_MATERIALIZE_CONTRACT" in prompt
    assert "STATUS: COMPLETE" in prompt


def test_complete_noop_allows_untracked_runtime_only(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    ledger = repo / "opsx" / "cursor-executor-ledger" / "run-1"
    ledger.mkdir(parents=True)
    (ledger / "evidence.json").write_text("{}", encoding="utf-8")
    assert not is_tree_clean(repo)
    assert is_product_tree_clean(repo)

    class Reply:
        def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
            return PromptInvocation(
                prompt=prompt,
                raw_output="STATUS: COMPLETE_NOOP\n",
                exit_code=0,
                session_id="s",
            )

    result = materialize_if_needed(
        repo_path=repo,
        adapter=Reply(),
        repair=RepairRunResult(
            completed=True,
            bug_batch_clear=True,
            unresolved=[],
            running_worker=None,
            raw_output=_repair_yes(),
            repaired_this_batch=True,
        ),
    )
    assert result.ok is True
    assert result.status == "COMPLETE_NOOP"
    assert result.reason == "COMPLETE_NOOP"
    assert current_head(repo) == sha_a


def test_parent_status_partial_is_blocked_not_unparseable(tmp_path: Path):
    repo, sha_a = init_repo(tmp_path)
    (repo / "src" / "app.js").write_text("FIXED\n", encoding="utf-8")

    class Reply:
        def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
            assert "TEMPORAL_MATERIALIZE_CONTRACT" in prompt
            return PromptInvocation(
                prompt=prompt,
                raw_output="**PARENT_STATUS:** `PARTIAL`\n",
                exit_code=0,
                session_id="s",
            )

    result = materialize_if_needed(
        repo_path=repo,
        adapter=Reply(),
        repair=RepairRunResult(
            completed=True,
            bug_batch_clear=True,
            unresolved=[],
            running_worker=None,
            raw_output=_repair_yes(),
            repaired_this_batch=True,
        ),
    )
    assert result.ok is False
    assert result.reason == "COMMIT_ALL_PARTIAL"
    assert result.status == COMMIT_MATERIALIZE_BLOCKED
    assert current_head(repo) == sha_a


def test_mock_mode_skips_adapter_commit_all(tmp_path: Path):
    repo, _sha = init_repo(tmp_path)
    (repo / "src" / "app.js").write_text("FIXED\n", encoding="utf-8")

    class Boom:
        def execute_prompt(self, prompt, *, step=None):
            raise AssertionError("mock must not invoke commit-all")

    result = materialize_if_needed(
        repo_path=repo,
        adapter=Boom(),
        repair=RepairRunResult(
            completed=True,
            bug_batch_clear=True,
            unresolved=[],
            running_worker=None,
            raw_output=_repair_yes(),
            repaired_this_batch=True,
        ),
        mock_mode=True,
    )
    assert result.ok is True
    assert result.status == "COMPLETE_NOOP"
    assert result.invoked is False


@pytest.mark.asyncio
async def test_temporal_commit_failure_blocks_second_review():
    configure_activities(
        ActivityConfig(mode="mock", repo_path=".", mock_scenario="scenario1")
    )

    @activity.defn(name="execute_commit_materialize")
    async def failing_materialize(config: dict) -> dict:
        return {
            "ok": False,
            "status": COMMIT_MATERIALIZE_BLOCKED,
            "reason": "COMMIT_ALL_BLOCKED",
            "head_before": "aaa",
            "head_after": "aaa",
            "tree_clean": False,
            "candidate_transitioned": False,
            "product_candidate_transitioned": False,
            "invoked": True,
            "raw_output": "STATUS: BLOCKED\n",
        }

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[BugbotRepairWorkflow],
            activities=[
                ensure_cursor_session,
                execute_bugbot_review,
                execute_opsx_repair,
                failing_materialize,
            ],
        ):
            result = await env.client.execute_workflow(
                BugbotRepairWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "scenario1",
                },
                id="workflow-commit-materialize-blocked",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

    assert result["status"] == COMMIT_MATERIALIZE_BLOCKED
    assert result["repair_rounds"] == 1
    assert result["review_runs"] == 1
    assert result["last_materialize"]["ok"] is False
