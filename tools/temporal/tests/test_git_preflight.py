"""Unit tests for git preflight evidence and review-prompt argv budgeting."""

from __future__ import annotations

import subprocess
from pathlib import Path

from config import GIT_PREFLIGHT_ARTIFACT_REL, MAX_REVIEW_PROMPT_CHARS
from git_preflight import (
    build_review_prompt,
    collect_git_preflight,
    format_git_preflight_artifact,
    prioritize_changed_files_for_diffs,
    write_git_preflight_artifact,
)


def test_collect_git_preflight_scoped_to_src_tests():
    repo = "C:/Users/vkita/my_code/game"
    result = collect_git_preflight(
        repo,
        base_ref="stage",
        path_prefixes=("src/", "tests/"),
    )
    assert result.ok is True
    assert result.review_scope == "SRC_TESTS_ONLY"
    assert all(
        path.startswith(("src/", "tests/"))
        for path in result.changed_files
    )
    artifact = format_git_preflight_artifact(result)
    assert "REVIEW_SCOPE: SRC_TESTS_ONLY" in artifact


def test_collect_git_preflight_against_stage():
    repo = "C:/Users/vkita/my_code/game"
    result = collect_git_preflight(repo, base_ref="stage")
    assert result.ok is True
    assert result.head_ref
    assert result.merge_base
    artifact = format_git_preflight_artifact(result)
    assert "REVIEW_SCOPE: FULL_BRANCH_DIFF" in artifact
    assert "WORKTREE_SCOPE: merge-base..working-tree" in artifact
    assert "git diff --stat" in artifact


def test_prioritize_changed_files_keeps_game_product_only():
    fixtures = tuple(f".cursor/commands/_fixtures/orch-bug-fix/f{i}.md" for i in range(30))
    openspec = tuple(
        f"openspec/changes/cross-device-wardrobe-refresh/file{i}.md" for i in range(5)
    )
    mixed = (
        "src/store/index.js",
        "tools/temporal/git_preflight.py",
        "backend/app/progress.py",
        "package.json",
    )
    selected = prioritize_changed_files_for_diffs(fixtures + openspec + mixed)
    assert "src/store/index.js" in selected
    assert "backend/app/progress.py" in selected
    assert any(path.startswith("openspec/changes/") for path in selected)
    assert "tools/temporal/git_preflight.py" not in selected
    assert "package.json" not in selected
    assert all(not path.startswith(".cursor/") for path in selected)
    assert selected[0].startswith(("src/", "backend/", "tests/", "openspec/changes/"))


def test_collect_git_preflight_includes_dirty_tracked_paths(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    base = repo / "base.txt"
    head = repo / "head.txt"
    dirty = repo / "dirty.txt"
    base.write_text("base\n", encoding="utf-8")
    head.write_text("head\n", encoding="utf-8")
    dirty.write_text("dirty\n", encoding="utf-8")

    def fake_run_git(repo_path: str, *args: str):
        import subprocess

        if args == ("rev-parse", "--verify", "stage"):
            return subprocess.CompletedProcess(args, 0, "stage-sha\n", "")
        if args == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "head-sha\n", "")
        if args == ("merge-base", "stage", "HEAD"):
            return subprocess.CompletedProcess(args, 0, "merge-base-sha\n", "")
        if args == ("diff", "--stat", "merge-base-sha"):
            return subprocess.CompletedProcess(args, 0, " dirty.txt | 1 +\n", "")
        if args == ("diff", "--name-only", "merge-base-sha"):
            return subprocess.CompletedProcess(args, 0, "dirty.txt\n", "")
        if args == ("ls-files", "--others", "--exclude-standard"):
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ("diff", "merge-base-sha", "--", "dirty.txt"):
            return subprocess.CompletedProcess(args, 0, "+dirty\n", "")
        return subprocess.CompletedProcess(args, 1, "", f"unexpected git args: {args}")

    monkeypatch.setattr("git_preflight._run_git", fake_run_git)
    result = collect_git_preflight(str(repo), base_ref="stage")
    assert result.ok is True
    assert "dirty.txt" in result.changed_files


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _init_argv_budget_preflight_repo(tmp_path: Path) -> Path:
    """Isolated repo: `stage` at base commit, HEAD ahead with product diffs."""
    repo = tmp_path / "argv_budget_repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "preflight-test@example.com")
    _run_git(repo, "config", "user.name", "preflight-test")

    src = repo / "src"
    src.mkdir()
    (src / "anchor.js").write_text("export const anchor = 1;\n", encoding="utf-8")
    _run_git(repo, "add", "src/anchor.js")
    _run_git(repo, "commit", "-m", "stage base")

    _run_git(repo, "branch", "stage")

    for index in range(6):
        body = "\n".join(f"// product chunk {index} line {line}" for line in range(120))
        (src / f"module_{index}.js").write_text(body + "\n", encoding="utf-8")
    backend = repo / "backend" / "app"
    backend.mkdir(parents=True)
    (backend / "feature.py").write_text(
        '"""Review target."""\n\n' + "VALUE = 42\n" * 80,
        encoding="utf-8",
    )
    _run_git(repo, "add", "src", "backend")
    _run_git(repo, "commit", "-m", "product lane diff")

    return repo


def test_build_review_prompt_stays_within_windows_argv_budget(tmp_path: Path):
    repo = _init_argv_budget_preflight_repo(tmp_path)
    repo_str = str(repo)

    preflight = collect_git_preflight(repo_str, base_ref="stage")
    assert preflight.ok is True
    assert preflight.merge_base
    assert preflight.head_ref
    assert preflight.merge_base != preflight.head_ref
    assert len(preflight.changed_files) > 0
    assert preflight.file_diffs

    prompt = build_review_prompt(preflight, repo_path=repo_str)
    assert len(prompt) <= MAX_REVIEW_PROMPT_CHARS
    assert f"PREFLIGHT_ARTIFACT: {GIT_PREFLIGHT_ARTIFACT_REL}" in prompt

    artifact_rel = write_git_preflight_artifact(repo_str, preflight)
    assert artifact_rel == GIT_PREFLIGHT_ARTIFACT_REL.replace("\\", "/")
    artifact_path = repo / artifact_rel
    assert artifact_path.is_file()
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert "POC_GIT_PREFLIGHT: VERIFIED" in artifact_text
    assert "REVIEW_SCOPE: FULL_BRANCH_DIFF" in artifact_text
    assert preflight.changed_files[0] in artifact_text
    assert any(path in artifact_text for path, _ in preflight.file_diffs)
    assert artifact_path.stat().st_size > len(prompt)
