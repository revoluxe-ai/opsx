"""Разбор реального ответа ``/opsx-commit-all`` без подмены вердикта.

Проверяет ``materialize_if_needed`` на временном git-репозитории: успешный
commit принимается только вместе с наблюдаемым SHA, проза и ``BUG_BATCH_CLEAR``
успехом не считаются, ошибка git сохраняется, чужой путь блокируется.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from commit_materialization import (
    current_head,
    materialize_if_needed,
    parse_commit_all_status,
)
from cursor_adapter import PromptInvocation
from lanes import BUGBOT_LANE, SECURITY_LANE
from models import RepairRunResult
from parsers import parse_bugbot_result, parse_security_review_result
from repair_evidence_identity import RepairEvidenceContext
from sdk_model_route import write_policy_for_step
from target_capabilities import authorized_repair_write_prefixes
from write_ownership import capture_worktree, evaluate_write_ownership

# Дословный result_text hop commit-all workflow bugbot-repair-poc-real
# run 01a16f8b-f6e2-785f-93de-31e2fe3214ac. Shell был недоступен, STATUS нет.
INCIDENT_PROSE = """Shell в этом turn недоступен — коммит не выполнен; ниже готовый локальный hop.

```
/workspace | branch feature/invest-yalta-max-funnel | /opsx-commit-all GO (product lane only; process lane SKIP — untracked opsx/cursor-executor-ledger/ and opsx/runs/temporal/* are ephemeral POC artifacts)

git status
git diff
git log -3 --oneline

git add src/stroy/consultation/invest_yalta/handler.py src/stroy/channels/max/invest_yalta/outbound_drain.py tests/integration/invest_yalta/test_max_funnel_composed_path.py
git commit -m "$(cat <<'EOF'
fix(invest-yalta-max): close MAX funnel parity gaps from Bugbot review

Align MAX turns with Telegram for slotsLoading resolution, phone normalization,
pending calendar promotion, and ambiguous outbound reconciliation after lease expiry.
EOF
)"
git status
```"""

_BUG_BATCH_CLEAR = (
    "STATUS: BUG_BATCH_CLEAR\n"
    "BUG_BATCH_CLEAR: YES\n"
    "REPAIRED_THIS_BATCH: YES\n"
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    product = repo / "src" / "stroy" / "pkg.py"
    product.parent.mkdir(parents=True)
    product.write_text("old\n", encoding="utf-8")
    _git(repo, "add", "src/stroy/pkg.py")
    _git(repo, "commit", "-m", "base")
    sha = current_head(repo)
    assert sha
    return repo, sha


def _stage_prefixes(monkeypatch) -> tuple[str, ...]:
    """Боевой source+test scope, без тестового override ``app/``."""

    monkeypatch.delenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", raising=False)
    monkeypatch.delenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", raising=False)
    monkeypatch.delenv("OPSX_TEMPORAL_TEST_SPEC_CHANGE_ROOTS", raising=False)
    prefixes = authorized_repair_write_prefixes()
    assert prefixes == ("src/stroy/", "tests/unit/", "tests/integration/")
    return prefixes


def _repair() -> RepairRunResult:
    return RepairRunResult(
        completed=True,
        bug_batch_clear=True,
        unresolved=[],
        running_worker=None,
        raw_output=_BUG_BATCH_CLEAR,
        repaired_this_batch=True,
    )


def _context(head_sha: str) -> RepairEvidenceContext:
    return RepairEvidenceContext(
        orchestration_execution_id="test-exec",
        workflow_id="test-wf",
        repair_number=1,
        review_fingerprint="test-fp",
        base_sha="stage",
        head_sha=head_sha,
        workflow_run_id="test-run",
    )


class _Reply:
    """Ответ commit-all. По умолчанию git не вызывает."""

    def __init__(self, raw: str, action=None) -> None:
        self.raw = raw
        self.action = action
        self.calls = 0

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        self.calls += 1
        assert step == "commit-all"
        if self.action is not None:
            self.action()
        return PromptInvocation(
            prompt=prompt,
            raw_output=self.raw,
            exit_code=0,
            session_id="session-test",
        )


def _materialize(repo: Path, raw: str, head: str, *, action=None, prefixes: tuple[str, ...] = ()):
    _ = head, prefixes
    return materialize_if_needed(
        repo_path=repo,
        adapter=_Reply(raw, action),
        repair=_repair(),
    )


def test_real_commit_is_accepted_only_with_observed_sha(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)
    target = repo / "src" / "stroy" / "pkg.py"

    def _commit() -> None:
        target.write_text("fixed\n", encoding="utf-8")
        _git(repo, "add", "src/stroy/pkg.py")
        _git(repo, "commit", "-m", "fix")

    result = _materialize(repo, "STATUS: COMPLETE\n", sha, action=_commit, prefixes=prefixes)
    head_after = current_head(repo)
    assert result.ok is True
    assert result.status == "COMPLETE"
    assert result.post_bound is True
    assert result.candidate_transitioned is True
    assert result.head_before == sha
    assert result.head_after == head_after
    assert head_after and head_after != sha
    assert _git(repo, "rev-parse", f"{head_after}^").stdout.strip() == sha
    assert target.read_text(encoding="utf-8") == "fixed\n"
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_parse_parent_status_markdown_alias() -> None:
    assert parse_commit_all_status("**PARENT_STATUS:** `PARTIAL`\n") == "PARTIAL"


def test_incident_prose_does_not_commit(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)
    target = repo / "src" / "stroy" / "pkg.py"
    target.write_text("dirty\n", encoding="utf-8")
    assert parse_commit_all_status(INCIDENT_PROSE) is None
    result = _materialize(repo, INCIDENT_PROSE, sha, prefixes=prefixes)
    assert result.ok is False
    assert result.status == "COMMIT_MATERIALIZE_BLOCKED"
    assert result.reason == "COMMIT_ALL_STATUS_UNPARSEABLE"
    assert result.raw_output == INCIDENT_PROSE
    assert result.head_before == sha
    assert result.head_after == sha
    assert current_head(repo) == sha
    assert result.candidate_transitioned is False
    assert "dirty" in target.read_text(encoding="utf-8")


def test_status_complete_without_commit_is_not_success(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)
    (repo / "src" / "stroy" / "pkg.py").write_text("dirty\n", encoding="utf-8")
    dirty = _materialize(repo, "STATUS: COMPLETE\n", sha, prefixes=prefixes)
    assert dirty.ok is False
    assert dirty.reason == "TREE_DIRTY_AFTER_COMMIT_ALL"
    assert current_head(repo) == sha

    clean_repo, clean_sha = _init_repo(tmp_path / "clean")
    unchanged = _materialize(clean_repo, "STATUS: COMPLETE\n", clean_sha, prefixes=prefixes)
    assert unchanged.ok is False
    assert unchanged.reason is not None
    assert unchanged.reason.startswith("COMMIT_HEAD_MISMATCH:")
    assert clean_sha in unchanged.reason
    assert current_head(clean_repo) == clean_sha


def test_malformed_status_is_rejected(tmp_path: Path) -> None:
    repo, sha = _init_repo(tmp_path)
    (repo / "src" / "stroy" / "pkg.py").write_text("dirty\n", encoding="utf-8")
    for raw in (
        "not a status\n",
        "STATUS:\n",
        "STATUS: PLAN_READY\n",
        "STATUS: COMPLETELY_DONE\n",
        "STATUS: COMMITTED\n",
        _BUG_BATCH_CLEAR,
    ):
        assert parse_commit_all_status(raw) is None
        result = _materialize(repo, raw, sha)
        assert result.ok is False
        assert result.reason == "COMMIT_ALL_STATUS_UNPARSEABLE"
        assert result.status != "COMPLETE"
        assert current_head(repo) == sha


def test_bug_batch_clear_is_not_materialize_or_review_pass() -> None:
    assert parse_commit_all_status(_BUG_BATCH_CLEAR) is None
    bugbot = parse_bugbot_result(_BUG_BATCH_CLEAR)
    security = parse_security_review_result(_BUG_BATCH_CLEAR)
    assert bugbot.clean is not True
    assert security.clean is not True
    assert SECURITY_LANE.materialize_activity == BUGBOT_LANE.materialize_activity
    assert write_policy_for_step("commit-all") == "MUTATING"
    assert write_policy_for_step("review") == "READ_ONLY"
    assert write_policy_for_step("repair") == "MUTATING"


def test_git_error_keeps_stderr(tmp_path: Path) -> None:
    repo, sha = _init_repo(tmp_path)
    (repo / "src" / "stroy" / "pkg.py").write_text("dirty\n", encoding="utf-8")
    shutil.rmtree(repo / ".git")
    result = _materialize(repo, "STATUS: COMPLETE\n", sha)
    assert result.ok is False
    assert result.reason is not None
    assert result.reason.startswith("COMMIT_ALL_GIT_ERROR:")
    assert "not a git repository" in result.reason
    assert "STATUS: COMPLETE" in result.raw_output
    assert result.candidate_transitioned is False


def test_orphan_head_mismatches_expected_parent(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)

    def _orphan() -> None:
        target = repo / "src" / "stroy" / "pkg.py"
        target.write_text("fixed\n", encoding="utf-8")
        _git(repo, "add", "src/stroy/pkg.py")
        tree = _git(repo, "write-tree").stdout.strip()
        orphan = _git(repo, "commit-tree", tree, "-m", "orphan").stdout.strip()
        _git(repo, "reset", "--hard", orphan)

    result = _materialize(repo, "STATUS: COMPLETE\n", sha, action=_orphan, prefixes=prefixes)
    assert result.ok is False
    assert result.reason is not None
    assert result.reason.startswith("COMMIT_HEAD_MISMATCH:")
    assert sha in result.reason
    assert current_head(repo) != sha
    assert result.head_after == current_head(repo)
    assert result.post_bound is False


def test_context_head_must_match_commit_parent(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)

    def _commit() -> None:
        (repo / "src" / "stroy" / "pkg.py").write_text("fixed\n", encoding="utf-8")
        _git(repo, "add", "src/stroy/pkg.py")
        _git(repo, "commit", "-m", "fix")

    result = materialize_if_needed(
        repo_path=repo,
        adapter=_Reply("STATUS: COMPLETE\n", _commit),
        repair=_repair(),
        evidence_context=_context("a" * 40),
        authorized_prefixes=prefixes,
    )
    assert result.ok is False
    assert result.post_bound is False
    assert result.reason == "input revision was not the commit parent"
    assert current_head(repo) != sha
    assert _git(repo, "rev-parse", "HEAD^").stdout.strip() == sha


def test_foreign_path_stays_outside_write_scope(tmp_path: Path, monkeypatch) -> None:
    repo, sha = _init_repo(tmp_path)
    prefixes = _stage_prefixes(monkeypatch)
    baseline = capture_worktree(repo)
    foreign = repo / "notes" / "foreign.txt"
    foreign.parent.mkdir()
    foreign.write_text("nope\n", encoding="utf-8")
    ownership = evaluate_write_ownership(
        repo,
        baseline=baseline,
        authorized_prefixes=prefixes,
    )
    assert ownership.blocked is True
    assert ownership.terminal == "BLOCKED_WRITE_OWNERSHIP"
    assert "notes/foreign.txt" in ownership.reason

    def _commit_foreign() -> None:
        (repo / "src" / "stroy" / "pkg.py").write_text("fixed\n", encoding="utf-8")
        _git(repo, "add", "src/stroy/pkg.py", "notes/foreign.txt")
        _git(repo, "commit", "-m", "too wide")

    result = _materialize(
        repo,
        "STATUS: COMPLETE\n",
        sha,
        action=_commit_foreign,
        prefixes=prefixes,
    )
    assert result.ok is False
    assert result.post_bound is False
    assert result.reason is not None
    assert "outside authorized write scope" in result.reason
    assert "notes/foreign.txt" in result.reason
    assert "BLOCKED_WRITE_OWNERSHIP" not in (result.status or "")
